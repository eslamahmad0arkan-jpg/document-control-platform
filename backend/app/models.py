"""Database schema. Mirrors DATABASE.md and database/schema.sql.

Google Drive remains the source of truth for files; this schema stores:
- identity/auth (users, google_accounts, google_tokens)
- projects and membership
- a synchronized metadata snapshot of folders/files (NOT copies)
- activities (Google-detected changes), notifications, audit log
- sync/watch state machines
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import backref, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Enumerated constants (kept as strings for DB portability)
# --------------------------------------------------------------------------

USER_ROLE_ADMIN = "ADMIN"
USER_ROLE_MANAGER = "PROJECT_MANAGER"
USER_ROLE_EDITOR = "EDITOR"
USER_ROLE_VIEWER = "VIEWER"

PROJECT_MEMBER_ROLES = (USER_ROLE_MANAGER, USER_ROLE_EDITOR, USER_ROLE_VIEWER)

ACTION_CREATED = "CREATED"
ACTION_MODIFIED = "MODIFIED"
ACTION_MOVED = "MOVED"
ACTION_RENAMED = "RENAMED"
ACTION_TRASHED = "TRASHED"
ACTION_UNTRASHED = "UNTRASHED"
ACTION_CREATE_FOLDER = "CREATE_FOLDER"
ACTION_UPLOAD_FILE = "UPLOAD_FILE"
ACTION_COMMENTED = "COMMENTED"
ACTION_LOCKED = "LOCKED"
ACTION_UNLOCKED = "UNLOCKED"
ACTION_LINKED = "LINKED"
ACTION_UNLINKED = "UNLINKED"
ACTION_STRUCTURE_CREATED = "STRUCTURE_CREATED"
ACTION_STRUCTURE_RENAMED = "STRUCTURE_RENAMED"
ACTION_STRUCTURE_DELETED = "STRUCTURE_DELETED"

ACTIVITY_SOURCE_GOOGLE = "GOOGLE_CHANGES"
ACTIVITY_SOURCE_PUSH = "GOOGLE_PUSH"
ACTIVITY_SOURCE_MANUAL = "MANUAL_RECONCILE"
ACTIVITY_SOURCE_APP = "APPLICATION"

STRUCTURE_STATUSES = ("DRAFT", "FOR_REVIEW", "APPROVED", "REJECTED",
                      "ISSUED", "SUPERSEDED")

SYNC_TARGET_TYPE_FILE = "FILE"
SYNC_TARGET_TYPE_FOLDER = "FOLDER"

NOTIFICATION_TYPES = (
    "file_created",
    "file_modified",
    "file_moved",
    "file_renamed",
    "file_trashed",
    "folder_created",
    "folder_modified",
    "project_added",
    "member_added",
    "sync_error",
    "file_locked",
    "file_unlocked",
    "file_commented",
    "mention",
    "approval_request",
    "approval_requested",
    "file_approved",
    "file_rejected",
)


# --------------------------------------------------------------------------
# Identity & authentication
# --------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(320), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, default="")
    picture = Column(Text, nullable=True)
    google_sub = Column(String(191), unique=True, nullable=True, index=True)
    role = Column(String(32), nullable=False, default=USER_ROLE_VIEWER)
    theme = Column(String(16), nullable=False, default="light")
    password_hash = Column(String(255), nullable=True)  # reserved; Google OAuth is primary
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    tokens = relationship("GoogleToken", back_populates="user", cascade="all, delete-orphan")
    memberships = relationship(
        "ProjectMember", back_populates="user",
        foreign_keys="ProjectMember.user_id", cascade="all, delete-orphan",
    )


class GoogleAccount(Base):
    __tablename__ = "google_accounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    google_sub = Column(String(191), unique=True, nullable=False, index=True)
    email = Column(String(320), nullable=False)
    name = Column(String(255), nullable=True)
    picture = Column(Text, nullable=True)

    tokens = relationship("GoogleToken", back_populates="account", cascade="all, delete-orphan")


class GoogleToken(Base):
    __tablename__ = "google_tokens"
    __table_args__ = (UniqueConstraint("user_id", "account_id", name="uq_token_user_account"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("google_accounts.id", ondelete="CASCADE"), nullable=False)
    access_token = Column(Text, nullable=False)   # encrypted at rest
    refresh_token = Column(Text, nullable=True)   # encrypted at rest
    id_token = Column(Text, nullable=True)        # encrypted at rest
    scope = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="tokens")
    account = relationship("GoogleAccount", back_populates="tokens")


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    google_folder_id = Column(String(191), unique=True, nullable=False, index=True)
    color = Column(String(16), nullable=True)
    status = Column(String(32), nullable=False, default="ACTIVE")  # ACTIVE | ARCHIVED
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # soft delete

    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    sync_state = relationship("SyncState", back_populates="project", uselist=False,
                              cascade="all, delete-orphan")
    watch_channels = relationship("WatchChannel", back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_member_project_user"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(32), nullable=False, default=USER_ROLE_VIEWER)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    project = relationship("Project", back_populates="members")
    user = relationship("User", foreign_keys=[user_id], back_populates="memberships")


# --------------------------------------------------------------------------
# Drive metadata snapshot (Google Drive remains source of truth)
# --------------------------------------------------------------------------

class Folder(Base):
    __tablename__ = "folders"
    __table_args__ = (
        Index("ix_folders_project_parent", "project_id", "parent_folder_id"),
        Index("ix_folders_project_trashed", "project_id", "trashed"),
        Index("ix_folders_path", "path"),
    )

    id = Column(Integer, primary_key=True)
    drive_id = Column(String(191), unique=True, nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_folder_id = Column(String(191), nullable=True, index=True)
    db_parent_id = Column(Integer, ForeignKey("folders.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(1024), nullable=False)
    path = Column(Text, nullable=False, default="")
    depth = Column(Integer, nullable=False, default=0)
    created_time = Column(DateTime(timezone=True), nullable=True)
    modified_time = Column(DateTime(timezone=True), nullable=True)
    trashed = Column(Boolean, nullable=False, default=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    children_folders = relationship("Folder", backref="db_parent", remote_side=[id])
    project = relationship("Project")


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_project_folder", "project_id", "folder_drive_id"),
        Index("ix_files_project_trashed", "project_id", "trashed"),
        Index("ix_files_path", "path"),
    )

    id = Column(Integer, primary_key=True)
    drive_id = Column(String(191), unique=True, nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    folder_drive_id = Column(String(191), nullable=True, index=True)
    db_folder_id = Column(Integer, ForeignKey("folders.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(1024), nullable=False, index=True)
    mime_type = Column(String(255), nullable=False, default="")
    extension = Column(String(64), nullable=True, index=True)
    size = Column(Integer, nullable=True, default=0)
    path = Column(Text, nullable=False, default="")
    drive_url = Column(Text, nullable=True)
    created_time = Column(DateTime(timezone=True), nullable=True)
    modified_time = Column(DateTime(timezone=True), nullable=True)
    modified_by_email = Column(String(320), nullable=True)
    modified_by_name = Column(String(255), nullable=True)
    trashed = Column(Boolean, nullable=False, default=False)
    capabilities = Column(JSON, nullable=True)  # Drive permissions for the syncing identity
    checksum = Column(String(64), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    folder = relationship("Folder", foreign_keys=[db_folder_id])


class FileLock(Base):
    """Document-control lock: a member takes a file 'out' for editing.

    ``released_at`` is NULL while the lock is active. Only one active lock
    may exist per file, enforced by the unique partial index below.
    """
    __tablename__ = "file_locks"
    __table_args__ = (
        Index("ix_file_locks_active", "drive_id", "released_at"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    drive_id = Column(String(191), nullable=False, index=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=True)
    locked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    released_at = Column(DateTime(timezone=True), nullable=True)
    released_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    locked_by = relationship("User", foreign_keys=[locked_by_user_id])
    released_by = relationship("User", foreign_keys=[released_by_user_id])
    file = relationship("File")


class FileComment(Base):
    __tablename__ = "file_comments"
    __table_args__ = (
        Index("ix_file_comments_file", "project_id", "drive_id", "created_at"),
        Index("ix_file_comments_parent", "parent_id"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    # drive_id == "" (empty) means a general / project-level discussion comment.
    drive_id = Column(String(191), nullable=False, default="", index=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=True)
    parent_id = Column(Integer, ForeignKey("file_comments.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    body = Column(Text, nullable=False)
    mentions = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    user = relationship("User")
    file = relationship("File")
    parent = relationship("FileComment", remote_side=[id])
    replies = relationship(
        "FileComment",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    attachments = relationship(
        "FileCommentAttachment",
        back_populates="comment",
        cascade="all, delete-orphan",
    )


class FileCommentAttachment(Base):
    """Uploaded file attached to a comment or reply (stored on local disk)."""
    __tablename__ = "file_comment_attachments"
    __table_args__ = (
        Index("ix_comment_attach_comment", "project_id", "comment_id"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    comment_id = Column(Integer, ForeignKey("file_comments.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(512), nullable=False)
    size = Column(Integer, nullable=False, default=0)
    content_type = Column(String(191), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    comment = relationship("FileComment", back_populates="attachments")
    uploader = relationship("User")


class FileApproval(Base):
    """Formal document approval: a member requests review/approval of a file;
    a reviewer approves or rejects. Ties into lock + notifications."""
    __tablename__ = "file_approvals"
    __table_args__ = (
        Index("ix_file_approvals_state", "project_id", "status"),
        Index("ix_file_approvals_file", "project_id", "drive_id"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    drive_id = Column(String(191), nullable=False, index=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=True)
    requested_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="PENDING", index=True)
    comment = Column(Text, nullable=True)
    decided_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    decided_comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    decided_at = Column(DateTime(timezone=True), nullable=True)

    requested_by = relationship("User", foreign_keys=[requested_by_user_id])
    reviewer = relationship("User", foreign_keys=[reviewer_user_id])
    decided_by = relationship("User", foreign_keys=[decided_by_user_id])
    file = relationship("File")
    project = relationship("Project")


# --------------------------------------------------------------------------
# Activity / notifications / audit
# --------------------------------------------------------------------------

class ProjectStructureNode(Base):
    """A node in the project's WBS / document tree (قسم ← فرع ← امتداد ← ...).

    Unlimited depth: a node references its parent; roots have ``parent_id=NULL``.
    Pure metadata — the Google Drive tree stays untouched. Real Drive items are
    attached to nodes through :class:`ProjectStructureLink`.
    """
    __tablename__ = "project_structure_nodes"
    __table_args__ = (
        Index("ix_structure_nodes_project", "project_id", "parent_id"),
        Index("ix_structure_nodes_code", "project_id", "code"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("project_structure_nodes.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(64), nullable=True)          # WBS-ish identifier (e.g. "A", "02.1")
    description = Column(Text, nullable=True)
    position = Column(Integer, nullable=False, default=0)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    children = relationship(
        "ProjectStructureNode", backref=backref("parent", remote_side=[id]),
        cascade="all, delete-orphan",
    )
    links = relationship("ProjectStructureLink", back_populates="node",
                         cascade="all, delete-orphan")
    creator = relationship("User")


class ProjectStructureLink(Base):
    """Attachment of a REAL Google Drive item (file or folder) to a structure node,
    carrying document-control metadata (Document Index number, revision, status).

    ``drive_id`` points at the live Drive object; ``Drive stays the source of
    truth``. If the item disappears from Drive, the link remains with a
    ``Trashed/missing`` marker via ``available=False``.
    """
    __tablename__ = "project_structure_links"
    __table_args__ = (
        UniqueConstraint("node_id", "drive_id", name="uq_structure_link_node_drive"),
        Index("ix_structure_links_project", "project_id"),
        Index("ix_structure_links_drive", "project_id", "drive_id"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("project_structure_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    drive_id = Column(String(191), nullable=False)
    target_type = Column(String(16), nullable=False, default=SYNC_TARGET_TYPE_FILE)  # FILE | FOLDER
    item_name = Column(String(1024), nullable=False, default="")   # snapshot name at link time
    doc_number = Column(String(128), nullable=True)                # Document Index number (e.g. DOC-001)
    wbs_code = Column(String(128), nullable=True)                  # WBS / index code (e.g. "CIV-01")
    revision = Column(String(64), nullable=True)                   # e.g. "R0", "A", "Rev 02"
    status = Column(String(32), nullable=False, default="DRAFT", index=True)
    description = Column(Text, nullable=True)
    position = Column(Integer, nullable=False, default=0)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    node = relationship("ProjectStructureNode", back_populates="links")
    creator = relationship("User")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_project_time", "project_id", "detected_at"),
        Index("ix_activities_target", "drive_id"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(32), nullable=False, index=True)
    target_type = Column(String(16), nullable=False)  # FILE | FOLDER
    target_name = Column(String(1024), nullable=False)
    drive_id = Column(String(191), nullable=True, index=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    folder_id = Column(Integer, ForeignKey("folders.id", ondelete="SET NULL"), nullable=True)
    file_path = Column(Text, nullable=True)
    folder_path = Column(Text, nullable=True)
    actor_email = Column(String(320), nullable=True)
    actor_name = Column(String(255), nullable=True)
    details = Column(JSON, nullable=True)
    source = Column(String(32), nullable=False, default=ACTIVITY_SOURCE_GOOGLE)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    activity_key = Column(String(40), unique=True, nullable=True, index=True)  # dedupe

    project = relationship("Project")
    user = relationship("User")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "is_read"),
        UniqueConstraint("user_id", "activity_id", name="uq_notification_user_activity"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_id = Column(Integer, ForeignKey("activities.id", ondelete="CASCADE"), nullable=True)
    type = Column(String(32), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    link = Column(Text, nullable=True)
    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    user = relationship("User")
    project = relationship("Project")
    activity = relationship("Activity")


class NotificationPref(Base):
    """Per-member, per-project notification switch.

    A member with no row is treated as enabled (default); a row with
    ``enabled=False`` opts that member out of that project's notifications.
    """
    __tablename__ = "notification_prefs"
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_notifpref_user_project"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    user = relationship("User")
    project = relationship("Project")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(64), nullable=False)
    resource_type = Column(String(32), nullable=True)
    resource_id = Column(String(191), nullable=True)
    details = Column(JSON, nullable=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    user = relationship("User")


# --------------------------------------------------------------------------
# Sync / watch state machines
# --------------------------------------------------------------------------

class SyncState(Base):
    __tablename__ = "sync_states"
    __table_args__ = (UniqueConstraint("project_id", name="uq_sync_project"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    start_page_token = Column(Text, nullable=True)
    last_cursor = Column(Text, nullable=True)
    last_full_scan_at = Column(DateTime(timezone=True), nullable=True)
    last_incremental_at = Column(DateTime(timezone=True), nullable=True)
    scanning_now = Column(Boolean, nullable=False, default=False)
    scan_progress = Column(Integer, nullable=False, default=0)
    scan_total = Column(Integer, nullable=False, default=0)
    scan_done = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    consecutive_errors = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="sync_state")


class WatchChannel(Base):
    __tablename__ = "watch_channels"
    __table_args__ = (UniqueConstraint("channel_id", name="uq_watch_channel_id"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_id = Column(String(191), nullable=False)
    resource_id = Column(String(191), nullable=True)
    resource_kind = Column(String(64), nullable=True)
    address = Column(Text, nullable=True)
    state = Column(String(32), nullable=False, default="ACTIVE")  # ACTIVE | EXPIRED | STOPPED
    expiration = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_renewed_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="watch_channels")


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)