"""Activity recording. Each detected Drive change becomes a first-class,
deduplicated Activity row and generates member notifications."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Activity, Notification, Project, User, utcnow


def _key(parts: list[str]) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def record_activity(db: Session, project: Project, user: User | None, action: str,
                    target_type: str, target_name: str, *, drive_id: str | None = None,
                    file_id: int | None = None, folder_id: int | None = None,
                    file_path: str | None = None, folder_path: str | None = None,
                    actor_email: str | None = None, actor_name: str | None = None,
                    details: dict | None = None, source: str = "GOOGLE_CHANGES",
                    detected_at: datetime | None = None,
                    notify_extra: dict | None = None) -> Activity:
    detected_at = detected_at or utcnow()
    dedupe = _key([
        str(project.id), drive_id or "?",
        action, source, actor_email or "", target_name,
        detected_at.isoformat(),
    ])
    if activity_known(db, dedupe):
        return db.query(Activity).filter(Activity.activity_key == dedupe).first()

    activity = Activity(
        project_id=project.id,
        user_id=user.id if user else None,
        action=action,
        target_type=target_type,
        target_name=target_name,
        drive_id=drive_id,
        file_id=file_id,
        folder_id=folder_id,
        file_path=file_path,
        folder_path=folder_path,
        actor_email=actor_email,
        actor_name=actor_name,
        details=details,
        source=source,
        detected_at=detected_at,
        activity_key=dedupe,
    )
    db.add(activity)
    db.flush()
    _notify_members(db, project, activity, notify_extra)
    return activity


def activity_known(db: Session, dedupe_key: str) -> bool:
    return db.query(Activity.id).filter(Activity.activity_key == dedupe_key).first() is not None


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

def _notify_members(db: Session, project: Project, activity: Activity,
                    notify_extra: dict | None = None) -> list[Notification]:
    from .notification_service import build_notification

    return build_notification(db, project, activity, extra=notify_extra)