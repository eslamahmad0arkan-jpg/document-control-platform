"""Activity timeline with filters, search, pagination, sorting."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_project_for_user
from ..models import Activity, File, Folder, Project, User
from ..schemas import ActivityOut, ActivityPage

router = APIRouter(prefix="/api/projects/{project_id}/activities", tags=["activities"])

_ACTIONS = {"CREATED", "MODIFIED", "MOVED", "RENAMED", "TRASHED", "UNTRASHED",
            "CREATE_FOLDER", "UPLOAD_FILE"}
_SORTS = {
    "detected_at": Activity.detected_at,
    "target_name": Activity.target_name,
    "action": Activity.action,
    "actor_name": Activity.actor_name,
}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.get("", response_model=ActivityPage)
def list_activities(project_id: int,
                    user_id: int | None = None,
                    action: str | None = None,
                    actor: str | None = None,
                    date_from: str | None = None,
                    date_to: str | None = None,
                    file: str | None = None,
                    folder: str | None = None,
                    ext: str | None = None,
                    q: str | None = None,
                    sort: str = "detected_at",
                    order: str = "desc",
                    page: int = Query(1, ge=1),
                    per_page: int = Query(50, ge=1, le=500),
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    query = db.query(Activity).filter(Activity.project_id == project_id)

    if user_id:
        query = query.filter(Activity.user_id == user_id)
    if action:
        query = query.filter(Activity.action == action.upper())
    if actor:
        query = query.filter(or_(Activity.actor_name.ilike(f"%{actor}%"),
                                 Activity.actor_email.ilike(f"%{actor}%")))
    d_from = _parse_dt(date_from)
    d_to = _parse_dt(date_to)
    if d_from:
        query = query.filter(Activity.detected_at >= d_from)
    if d_to:
        query = query.filter(Activity.detected_at < d_to)
    if file:
        query = query.filter(Activity.target_name.ilike(f"%{file}%"))
    if folder:
        query = query.filter(or_(Activity.folder_path.ilike(f"%{folder}%"),
                                 Activity.file_path.ilike(f"%{folder}%")))
    if ext:
        query = query.filter(Activity.details["ext"].as_string() == ext.lstrip(".").lower())
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Activity.target_name.ilike(like),
                                 Activity.file_path.ilike(like),
                                 Activity.folder_path.ilike(like)))

    total = query.count()
    sort_col = _SORTS.get(sort, Activity.detected_at)
    order_by = sort_col.desc() if order == "desc" else sort_col.asc()
    items = (
        query.order_by(order_by, Activity.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return ActivityPage(
        items=[ActivityOut.model_validate(a) for a in items],
        total=total, page=page, per_page=per_page,
        filters={
            "user_id": user_id, "action": action, "actor": actor,
            "date_from": date_from, "date_to": date_to,
            "file": file, "folder": folder, "ext": ext, "q": q,
        },
    )


@router.get("/users")
def activity_users(project_id: int, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Distinct actors across a project's activities (for filter dropdowns)."""
    project, _m, _r = get_project_for_user(project_id, user, db)
    rows = (
        db.query(Activity.actor_email, Activity.actor_name, Activity.user_id)
        .filter(Activity.project_id == project_id)
        .distinct()
        .all()
    )
    out = []
    for email, name, uid in rows:
        out.append({"key": email or f"u{uid}", "name": (name or email or f"User {uid}")})
    out.sort(key=lambda x: x["name"].lower())
    return out