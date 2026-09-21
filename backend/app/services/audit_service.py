"""Application audit log service — records actions performed inside the app.
Distinct from Google Drive activities stored in the activities table."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog, User


def record_audit(db: Session, *, user: User | None, action: str,
                 project_id: int | None = None, resource_type: str | None = None,
                 resource_id: str | None = None, details: dict | None = None,
                 ip: str | None = None, user_agent: str | None = None) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        project_id=project_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip=ip,
        user_agent=user_agent,
    )
    db.add(entry)
    return entry