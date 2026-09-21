"""Pydantic request/response schemas (the API contract)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Auth ---------------------------------------------------------------------

class LoginUrlResponse(BaseModel):
    url: str


class UserPublic(ORMModel):
    id: int
    email: str
    name: str
    picture: str | None
    role: str
    theme: str
    is_active: bool
    last_login_at: datetime | None


class MeResponse(BaseModel):
    user: UserPublic
    unread_notifications: int


# --- Projects ---------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    google_folder_id: str = Field(min_length=1, max_length=191)
    description: str | None = None
    color: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    color: str | None = None
    status: Literal["ACTIVE", "ARCHIVED"] | None = None


class ProjectSummary(ORMModel):
    id: int
    name: str
    description: str | None
    google_folder_id: str
    color: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectRole(BaseModel):
    role: Literal["PROJECT_MANAGER", "EDITOR", "VIEWER"]


class MemberOut(BaseModel):
    user_id: int
    email: str
    name: str
    role: str
    added_at: datetime


class MemberStat(MemberOut):
    activities: int = 0
    uploads: int = 0
    comments: int = 0
    locks_active: int = 0
    last_activity_at: datetime | None = None


class ProjectDetail(ProjectSummary):
    role: str
    members: list[MemberOut]
    sync: "SyncStatusOut | None" = None
    stats: "ProjectStats | None" = None


class ScanProgress(BaseModel):
    phase: str
    scanning: bool
    progress: int
    total: int
    done: int
    last_error: str | None


class SyncStatusOut(BaseModel):
    phase: str
    scanning_now: bool
    progress: int
    total: int
    done: int
    last_full_scan_at: datetime | None
    last_incremental_at: datetime | None
    last_error: str | None
    consecutive_errors: int


class ProjectStats(BaseModel):
    total_files: int
    total_folders: int
    files_added_today: int
    files_modified_today: int
    activities_24h: int
    active_users: int
    total_size: int
    truncated: bool


# --- Explorer ---------------------------------------------------------------

class ExplorerNode(BaseModel):
    id: int | None = None
    drive_id: str
    type: Literal["FOLDER", "FILE"]
    name: str
    mime_type: str = ""
    extension: str | None = None
    size: int | None = None
    path: str = ""
    drive_url: str | None = None
    modified_time: datetime | None = None
    modified_by_name: str | None = None
    modified_by_email: str | None = None
    created_time: datetime | None = None
    capabilities: dict | None = None
    child_count: int = 0
    child_files: int = 0
    child_folders: int = 0
    parent_folder_id: str | None = None
    folder_drive_id: str | None = None
    lock: FileLockOut | None = None


class Breadcrumb(BaseModel):
    drive_id: str
    name: str


class ExplorerResponse(BaseModel):
    project_id: int
    current: ExplorerNode | None
    breadcrumbs: list[Breadcrumb]
    folders: list[ExplorerNode]
    files: list[ExplorerNode]
    total_folders: int
    total_files: int
    search_active: bool = False


class CreateFolderRequest(BaseModel):
    parent_id: str | None = None
    name: str = Field(min_length=1, max_length=255)


class RenameRequest(BaseModel):
    drive_id: str
    name: str = Field(min_length=1, max_length=255)


class MoveRequest(BaseModel):
    drive_id: str
    parent_id: str | None = None


class TrashRequest(BaseModel):
    drive_id: str


# --- Document control (locks & comments) ------------------------------------

class FileLockOut(BaseModel):
    id: int
    drive_id: str
    file_id: int | None
    locked_by_user_id: int
    locked_by_name: str
    locked_by_email: str
    comment: str | None
    created_at: datetime
    released_at: datetime | None = None
    released_by_user_id: int | None = None


class LockRequest(BaseModel):
    comment: str | None = None
    force: bool = False  # true = take over an existing lock


class CommentAttachmentOut(BaseModel):
    id: int
    filename: str
    size: int
    content_type: str | None
    download_url: str
    created_at: datetime


class CommentOut(BaseModel):
    id: int
    drive_id: str
    file_id: int | None
    parent_id: int | None
    user_id: int
    user_name: str
    user_email: str
    body: str
    mentions: list[int] = []
    attachments: list[CommentAttachmentOut] = []
    reply_count: int = 0
    created_at: datetime


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    parent_id: int | None = None
    mention_ids: list[int] = []


class FileApprovalOut(BaseModel):
    id: int
    drive_id: str
    file_id: int | None
    file_name: str | None
    status: str
    comment: str | None
    requested_by_user_id: int
    requested_by_name: str
    requested_by_email: str
    reviewer_user_id: int | None
    reviewer_name: str | None
    decided_by_user_id: int | None
    decided_comment: str | None
    created_at: datetime
    decided_at: datetime | None


class FileApprovalCreate(BaseModel):
    reviewer_user_id: int
    comment: str | None = None


class FileApprovalDecision(BaseModel):
    status: str          # APPROVED | REJECTED
    comment: str | None = None


# --- Project structure (WBS / document tree) ---------------------------------

class StructureNodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None
    code: str | None = Field(default=None, max_length=64)
    description: str | None = None
    position: int | None = None


class StructureNodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    code: str | None = Field(default=None, max_length=64)
    description: str | None = None
    position: int | None = None


class StructureLinkCreate(BaseModel):
    drive_id: str = Field(min_length=1, max_length=191)
    target_type: Literal["FILE", "FOLDER"] = "FILE"
    doc_number: str | None = Field(default=None, max_length=128)
    wbs_code: str | None = Field(default=None, max_length=128)
    revision: str | None = Field(default=None, max_length=64)
    status: str = "DRAFT"
    description: str | None = None


class StructureLinkUpdate(BaseModel):
    doc_number: str | None = Field(default=None, max_length=128)
    wbs_code: str | None = Field(default=None, max_length=128)
    revision: str | None = Field(default=None, max_length=64)
    status: str | None = None
    description: str | None = None
    position: int | None = None


class StructureNodeOut(ORMModel):
    id: int
    project_id: int
    parent_id: int | None
    name: str
    code: str | None
    description: str | None
    position: int
    created_at: datetime
    updated_at: datetime


class StructureLinkOut(ORMModel):
    id: int
    project_id: int
    node_id: int
    drive_id: str
    target_type: str
    item_name: str
    doc_number: str | None
    wbs_code: str | None
    revision: str | None
    status: str
    description: str | None
    position: int
    created_at: datetime
    updated_at: datetime
    available: bool = True        # still present in the Drive snapshot
    size: int | None = None
    extension: str | None = None
    mime_type: str | None = None
    modified_time: datetime | None = None


class StructureNodeDetail(StructureNodeOut):
    links: list[StructureLinkOut] = []
    children: list[int] = []      # direct child ids
    descendants: int = 0          # total nodes below


class StructureTree(StructureNodeOut):
    children: list["StructureTree"] = []


# --- Activities ------------------------------------------------------------

class ActivityOut(ORMModel):
    id: int
    project_id: int
    user_id: int | None
    action: str
    target_type: str
    target_name: str
    drive_id: str | None
    file_path: str | None
    folder_path: str | None
    actor_email: str | None
    actor_name: str | None
    details: dict | None
    source: str
    detected_at: datetime


class ActivityPage(BaseModel):
    items: list[ActivityOut]
    total: int
    page: int
    per_page: int
    filters: dict


class NotificationOut(ORMModel):
    id: int
    project_id: int
    activity_id: int | None
    type: str
    title: str
    description: str | None
    link: str | None
    is_read: bool
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    total: int
    unread: int
    page: int
    per_page: int


class NotificationPrefUpdate(BaseModel):
    enabled: bool


# --- Reports ---------------------------------------------------------------

class ReportOverview(BaseModel):
    date_from: datetime | None
    date_to: datetime | None
    total_files: int
    total_folders: int
    new_files: int
    modified_files: int
    trashed_files: int
    total_activities: int
    active_users: int
    total_size: int


class ReportCategory(BaseModel):
    key: str
    label: str
    count: int
    extra: dict = {}


class ActivityReport(BaseModel):
    by_day: list[ReportCategory]
    by_user: list[ReportCategory]
    by_action: list[ReportCategory]
    by_folder: list[ReportCategory]


class FileReport(BaseModel):
    by_extension: list[ReportCategory]
    by_folder: list[ReportCategory]
    recent_files: list[ExplorerNode]


class ReportsResponse(BaseModel):
    overview: ReportOverview
    activity: ActivityReport
    files: FileReport


# --- Search ---------------------------------------------------------------

class SearchResultFile(BaseModel):
    drive_id: str
    type: str
    name: str
    path: str
    extension: str | None = None
    modified_time: datetime | None = None
    drive_url: str | None = None


class SearchResultActivity(BaseModel):
    id: int
    action: str
    target_name: str
    target_type: str
    detected_at: datetime
    folder_path: str | None = None


class SearchResponse(BaseModel):
    items: list[Any]
    total: int
    q: str


# --- Admin / misc ----------------------------------------------------------

class AdminUser(ORMModel):
    id: int
    email: str
    name: str
    picture: str | None
    role: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    projects_count: int = 0


class RoleUpdate(BaseModel):
    role: Literal["ADMIN", "PROJECT_MANAGER", "EDITOR", "VIEWER"]
    is_active: bool | None = None


class SettingsUpdate(BaseModel):
    theme: Literal["light", "dark"] | None = None


class AuditEntry(ORMModel):
    id: int
    user_id: int | None
    project_id: int | None
    action: str
    resource_type: str | None
    resource_id: str | None
    details: dict | None
    ip: str | None
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int
    page: int
    per_page: int


class WorkerStatus(BaseModel):
    running: bool
    interval_seconds: int
    enabled: bool
    last_loop_at: datetime | None


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    version: str = "1.0.0"


ProjectDetail.model_rebuild()