"""Notification center endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_project_for_user
from ..errors import NotFoundError
from ..models import NotificationPref, Project, ProjectMember, User, USER_ROLE_ADMIN
from ..schemas import NotificationOut, NotificationPage, NotificationPrefUpdate
from ..services.notification_service import (
    list_notifications, mark_all_read, mark_read, unread_count,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage)
def notifications(project_id: int | None = None, unread_only: bool = False,
                  page: int = Query(1, ge=1), per_page: int = Query(25, ge=1, le=200),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if project_id:
        get_project_for_user(project_id, user, db)
    items, total = list_notifications(
        db, user.id, project_id=project_id, unread_only=unread_only,
        page=page, per_page=per_page,
    )
    return NotificationPage(
        items=[NotificationOut.model_validate(n) for n in items],
        total=total, unread=unread_count(db, user.id, project_id),
        page=page, per_page=per_page,
    )


@router.get("/unread-count")
def unread(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"total": unread_count(db, user.id)}


@router.get("/prefs")
def get_prefs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return notification preferences for every project the caller can access."""
    project_ids = [
        pid for (pid,) in db.query(ProjectMember.project_id)
        .filter(ProjectMember.user_id == user.id).distinct().all()
    ]
    if user.role == USER_ROLE_ADMIN:
        project_ids += [
            pid for (pid,) in db.query(Project.id)
            .filter(Project.deleted_at.is_(None)).all()
        ]
    project_ids = list(dict.fromkeys(project_ids))
    if not project_ids:
        return {"items": []}
    projects = {
        p.id: p.name for p in db.query(Project)
        .filter(Project.id.in_(project_ids), Project.deleted_at.is_(None)).all()
    }
    prefs = {
        row.project_id: row.enabled
        for row in db.query(NotificationPref)
        .filter(NotificationPref.user_id == user.id,
                NotificationPref.project_id.in_(project_ids)).all()
    }
    return {"items": [
        {"project_id": pid, "project_name": projects[pid],
         "enabled": prefs.get(pid, True)}
        for pid in projects
    ]}


@router.patch("/prefs/{project_id}")
def set_pref(project_id: int, payload: NotificationPrefUpdate,
             user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Toggle notification delivery for a single project."""
    get_project_for_user(project_id, user, db)
    row = (
        db.query(NotificationPref)
        .filter(NotificationPref.user_id == user.id,
                NotificationPref.project_id == project_id)
        .first()
    )
    if row:
        row.enabled = payload.enabled
    else:
        db.add(NotificationPref(
            user_id=user.id, project_id=project_id, enabled=payload.enabled))
    db.commit()
    return {"project_id": project_id, "enabled": payload.enabled}


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_one_read(notification_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    row = mark_read(db, user.id, notification_id)
    if not row:
        raise NotFoundError("Notification not found.")
    db.commit()
    return NotificationOut.model_validate(row)


@router.post("/read-all", status_code=204)
def mark_all(project_id: int | None = None, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    if project_id:
        get_project_for_user(project_id, user, db)
    mark_all_read(db, user.id, project_id)
    db.commit()