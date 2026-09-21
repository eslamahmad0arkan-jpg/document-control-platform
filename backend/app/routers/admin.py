"""Admin endpoints: user management, audit log, worker status."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_admin
from ..errors import NotFoundError
from ..models import AuditLog, ProjectMember, User
from ..schemas import AdminUser, AuditEntry, AuditPage, RoleUpdate, WorkerStatus
from ..services import monitor
from ..services.audit_service import record_audit

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[AdminUser])
def list_users(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(User).order_by(User.created_at.asc()).all()
    counts = dict(
        db.query(ProjectMember.user_id, func.count(ProjectMember.id))
        .group_by(ProjectMember.user_id).all()
    )
    return [
        AdminUser(id=u.id, email=u.email, name=u.name, picture=u.picture,
                  role=u.role, is_active=u.is_active,
                  last_login_at=u.last_login_at, created_at=u.created_at,
                  projects_count=counts.get(u.id, 0))
        for u in rows
    ]


@router.patch("/users/{user_id}", response_model=AdminUser)
def update_user(user_id: int, payload: RoleUpdate, request: Request,
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise NotFoundError("User not found.")
    if target.id == admin.id and payload.role != "ADMIN":
        from ..errors import ForbiddenError

        raise ForbiddenError("You cannot demote your own account.")
    target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active
    record_audit(db, user=admin, action="USER_ROLE_CHANGED",
                 resource_type="USER", resource_id=str(target.id),
                 details={"role": payload.role, "is_active": payload.is_active},
                 ip=request.client.host if request.client else None,
                 user_agent=request.headers.get("user-agent"))
    db.commit()
    return AdminUser(id=target.id, email=target.email, name=target.name,
                     picture=target.picture, role=target.role,
                     is_active=target.is_active, last_login_at=target.last_login_at,
                     created_at=target.created_at, projects_count=_count(db, target.id))


def _count(db: Session, user_id: int) -> int:
    return db.query(func.count(ProjectMember.id)).filter(
        ProjectMember.user_id == user_id).scalar() or 0


@router.get("/audit", response_model=AuditPage)
def audit_log(user: User = Depends(require_admin),
              page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=500),
              action: str | None = None, user_id: int | None = None,
              db: Session = Depends(get_db)):
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    total = query.count()
    items = (
        query.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * per_page).limit(per_page).all()
    )
    return AuditPage(items=[AuditEntry.model_validate(a) for a in items],
                     total=total, page=page, per_page=per_page)


@router.get("/worker", response_model=WorkerStatus)
def worker_status(user: User = Depends(require_admin)):
    from ..config import settings

    status = getattr(user_instance_hack(), "monitor_status", {})
    last = status.get("last_loop_at")
    return WorkerStatus(
        running=status.get("running", False),
        interval_seconds=settings.SYNC_INTERVAL_SECONDS,
        enabled=settings.MONITOR_ENABLED,
        last_loop_at=_parse_iso(last),
    )


def user_instance_hack():
    from ..main import app

    return app


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None