"""Notification center: builds unread notifications for project members from
recorded activities, and exposes query/mutation helpers."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Activity, Notification, NotificationPref, Project, ProjectMember, User


_SPECS = {
    "CREATED": ("file_created", "New file"),
    "MODIFIED": ("file_modified", "File modified"),
    "MOVED": ("file_moved", "File moved"),
    "RENAMED": ("file_renamed", "File renamed"),
    "TRASHED": ("file_trashed", "File trashed"),
    "UNTRASHED": ("file_untrashed", "File restored"),
    "FOLDER_CREATED": ("folder_created", "New folder"),
    "FOLDER_MODIFIED": ("folder_modified", "Folder updated"),
    "FOLDER_MOVED": ("folder_moved", "Folder moved"),
    "FOLDER_RENAMED": ("folder_renamed", "Folder renamed"),
    "FOLDER_TRASHED": ("folder_trashed", "Folder trashed"),
    "SYNC_ERROR": ("sync_error", "Sync error"),
    "PROJECT_ADDED": ("project_added", "Project connected"),
    "MEMBER_ADDED": ("member_added", "Member added"),
    "COMMENTED": ("file_commented", "New comment"),
    "LOCKED": ("file_locked", "File locked"),
    "UNLOCKED": ("file_unlocked", "File unlocked"),
    "APPROVAL_REQUESTED": ("approval_requested", "Approval requested"),
    "APPROVED": ("file_approved", "File approved"),
    "REJECTED": ("file_rejected", "File rejected"),
}


def build_notification(db: Session, project: Project, activity: Activity,
                       extra: dict | None = None) -> list[Notification]:
    notif_type, headline = _SPECS.get(activity.action, ("file_modified", "Activity"))
    exclude_user_id = (extra or {}).get("exclude_user_id")
    members = (
        db.query(ProjectMember.user_id)
        .filter(ProjectMember.project_id == project.id)
        .all()
    )
    # Members may mute a project; absence of a row means "enabled".
    prefs = dict(
        db.query(NotificationPref.user_id, NotificationPref.enabled)
        .filter(NotificationPref.project_id == project.id)
        .all()
    )
    link = f"/projects/{project.id}/explorer"
    if activity.drive_id:
        link += f"?drive={activity.drive_id}"
    created: list[Notification] = []
    for (user_id,) in members:
        if user_id == exclude_user_id:
            continue
        if prefs.get(user_id, True) is False:
            continue
        exists = (
            db.query(Notification.id)
            .filter(
                Notification.user_id == user_id,
                Notification.activity_id == activity.id,
            )
            .first()
        )
        if exists:
            continue
        description = f"{activity.target_name}"
        if activity.folder_path:
            description += f" — {activity.folder_path}"
        if activity.actor_name:
            description += f" by {activity.actor_name}"
        row = Notification(
            user_id=user_id,
            project_id=project.id,
            activity_id=activity.id,
            type=notif_type,
            title=f"{headline}: {activity.target_name}",
            description=description,
            link=link,
            is_read=False,
        )
        db.add(row)
        created.append(row)
    db.flush()
    return created


def unread_count(db: Session, user_id: int, project_id: int | None = None) -> int:
    q = db.query(Notification.id).filter(
        Notification.user_id == user_id, Notification.is_read.is_(False)
    )
    if project_id is not None:
        q = q.filter(Notification.project_id == project_id)
    return q.count()


def list_notifications(db: Session, user_id: int, *, project_id: int | None = None,
                       unread_only: bool = False, page: int = 1, per_page: int = 20):
    q = db.query(Notification).filter(Notification.user_id == user_id)
    if project_id:
        q = q.filter(Notification.project_id == project_id)
    if unread_only:
        q = q.filter(Notification.is_read.is_(False))
    total = q.count()
    items = (
        q.order_by(Notification.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return items, total


def mark_read(db: Session, user_id: int, notification_id: int) -> Notification | None:
    row = db.query(Notification).filter(
        Notification.id == notification_id, Notification.user_id == user_id
    ).first()
    if row:
        row.is_read = True
    return row


def mark_all_read(db: Session, user_id: int, project_id: int | None = None) -> int:
    q = db.query(Notification).filter(
        Notification.user_id == user_id, Notification.is_read.is_(False)
    )
    if project_id:
        q = q.filter(Notification.project_id == project_id)
    return q.update({"is_read": True}, synchronize_session=False)