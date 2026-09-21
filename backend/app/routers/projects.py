"""Project management: connect Drive folders, dashboards, members, sync triggers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_project_for_user
from ..errors import ConflictError, ForbiddenError, NotFoundError, AppError
from ..models import (
    ACTION_UPLOAD_FILE, Activity, File, FileComment, FileLock, Folder, Project,
    ProjectMember, SyncState, User,
    USER_ROLE_ADMIN, USER_ROLE_MANAGER, USER_ROLE_VIEWER, utcnow,
    USER_ROLE_EDITOR,
)
from ..schemas import (
    MemberOut, MemberStat, ProjectCreate, ProjectDetail, ProjectStats, ProjectSummary,
    ProjectUpdate, ScanProgress, SyncStatusOut,
)
from ..services import monitor
from ..services.audit_service import record_audit
from ..services.explorer import explorer_listing
from ..services.google import oauth
from ..services.google.drive import DriveClient, FOLDER_MIME
from ..services.sync import pick_sync_identity

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _role_at_least(role, minimum):
    rank = {USER_ROLE_ADMIN: 4, USER_ROLE_MANAGER: 3, USER_ROLE_EDITOR: 2, USER_ROLE_VIEWER: 1}
    return rank.get(role, 0) >= rank.get(minimum, 0)


def _sync_out(state: SyncState | None) -> SyncStatusOut:
    if state is None:
        return SyncStatusOut(phase="pending", scanning_now=False, progress=0,
                             total=0, done=0, last_full_scan_at=None,
                             last_incremental_at=None, last_error=None,
                             consecutive_errors=0)
    if state.scanning_now:
        phase = "scanning"
    elif state.consecutive_errors:
        phase = "error"
    else:
        phase = "idle"
    return SyncStatusOut(
        phase=phase, scanning_now=state.scanning_now,
        progress=state.scan_progress, total=state.scan_total, done=state.scan_done,
        last_full_scan_at=state.last_full_scan_at,
        last_incremental_at=state.last_incremental_at,
        last_error=state.last_error, consecutive_errors=state.consecutive_errors,
    )


def _build_stats(db: Session, project_id: int) -> ProjectStats:
    now = utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since_24h = now - timedelta(hours=24)

    total_files = db.query(func.count(File.id)).filter(
        File.project_id == project_id, File.trashed.is_(False)).scalar() or 0
    total_folders = db.query(func.count(Folder.id)).filter(
        Folder.project_id == project_id, Folder.trashed.is_(False)).scalar() or 0
    total_size = db.query(func.coalesce(func.sum(File.size), 0)).filter(
        File.project_id == project_id, File.trashed.is_(False)).scalar() or 0

    added_today = db.query(func.count(Activity.id)).filter(
        Activity.project_id == project_id, Activity.action == "CREATED",
        Activity.target_type == "FILE", Activity.detected_at >= start_of_day).scalar() or 0
    modified_today = db.query(func.count(Activity.id)).filter(
        Activity.project_id == project_id, Activity.action == "MODIFIED",
        Activity.target_type == "FILE", Activity.detected_at >= start_of_day).scalar() or 0
    activities_24h = db.query(func.count(Activity.id)).filter(
        Activity.project_id == project_id, Activity.detected_at >= since_24h).scalar() or 0

    actor_rows = db.query(Activity.actor_email, Activity.user_id).filter(
        Activity.project_id == project_id, Activity.detected_at >= since_24h).all()
    actors = set()
    for email, uid in actor_rows:
        if uid:
            actors.add(f"u{uid}")
        elif email:
            actors.add(email)
    return ProjectStats(
        total_files=total_files, total_folders=total_folders,
        files_added_today=added_today, files_modified_today=modified_today,
        activities_24h=activities_24h, active_users=len(actors),
        total_size=total_size, truncated=False,
    )


def _member_rows(db: Session, project_id: int) -> list[MemberOut]:
    rows = (
        db.query(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .filter(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.created_at.asc())
        .all()
    )
    return [
        MemberOut(user_id=pm.user_id, email=u.email, name=u.name, role=pm.role,
                  added_at=pm.created_at)
        for pm, u in rows
    ]


# --------------------------------------------------------------------------

@router.get("", response_model=list[ProjectSummary])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == USER_ROLE_ADMIN:
        projects = db.query(Project).filter(Project.deleted_at.is_(None)).all()
    else:
        projects = (
            db.query(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .filter(ProjectMember.user_id == user.id, Project.deleted_at.is_(None))
            .all()
        )
    return [ProjectSummary.model_validate(p) for p in projects]


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(payload: ProjectCreate, request: Request,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not payload.google_folder_id.strip():
        raise AppError("A Google Drive folder ID is required.")
    existing = db.query(Project).filter(
        Project.google_folder_id == payload.google_folder_id,
        Project.deleted_at.is_(None),
    ).first()
    if existing:
        raise ConflictError("This Google Drive folder is already connected to a project.")

    _, drive = pick_sync_identity(db, Project(id=0, google_folder_id=payload.google_folder_id,
                                              name=payload.name, created_by=user.id), user)
    try:
        meta = drive.get_file(payload.google_folder_id)
    except Exception:
        raise AppError(
            "Could not read this folder from Google Drive. Make sure the Folder ID is correct "
            "and that your Google account has access to it."
        )
    if meta.get("mimeType") != FOLDER_MIME:
        raise AppError("The selected Google Drive item is not a folder.")

    project = Project(
        name=payload.name or meta.get("name", "Untitled Project"),
        description=payload.description,
        google_folder_id=payload.google_folder_id,
        color=payload.color,
        status="ACTIVE",
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id,
                         role=USER_ROLE_MANAGER, added_by=user.id))
    record_audit(db, user=user, action="PROJECT_CREATED", project_id=project.id,
                 resource_type="PROJECT", resource_id=str(project.id),
                 details={"folder_id": payload.google_folder_id}, ip=_ip(request),
                 user_agent=request.headers.get("user-agent"))
    db.commit()

    monitor.trigger_async_sync(project.id)

    return _project_detail(db, project, user.id)


@router.get("/{project_id}", response_model=ProjectDetail)
def project_detail(project_id: int, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    project, _member, eff = get_project_for_user(project_id, user, db)
    return _project_detail(db, project, user.id)


def _project_detail(db: Session, project: Project, user_id: int) -> ProjectDetail:
    member = db.query(ProjectMember).filter(
        ProjectMember.project_id == project.id, ProjectMember.user_id == user_id).first()
    eff_role = USER_ROLE_MANAGER if member is None else member.role
    state = db.query(SyncState).filter(SyncState.project_id == project.id).first()
    return ProjectDetail(
        id=project.id, name=project.name, description=project.description,
        google_folder_id=project.google_folder_id, color=project.color,
        status=project.status, created_at=project.created_at, updated_at=project.updated_at,
        role=eff_role, members=_member_rows(db, project.id),
        sync=_sync_out(state), stats=_build_stats(db, project.id),
    )


@router.get("/{project_id}/sync", response_model=SyncStatusOut)
def sync_status(project_id: int, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    state = db.query(SyncState).filter(SyncState.project_id == project_id).first()
    return _sync_out(state)


@router.post("/{project_id}/sync", response_model=SyncStatusOut)
def trigger_sync(project_id: int, request: Request,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    record_audit(db, user=user, action="SYNC_TRIGGERED", project_id=project_id,
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    monitor.trigger_async_sync(project_id)
    state = db.query(SyncState).filter(SyncState.project_id == project_id).first()
    return _sync_out(state)


@router.get("/{project_id}/scan-progress", response_model=ScanProgress)
def scan_progress(project_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    state = db.query(SyncState).filter(SyncState.project_id == project_id).first()
    if state is None:
        return ScanProgress(phase="pending", scanning=False, progress=0,
                            total=0, done=0, last_error=None)
    return ScanProgress(
        phase="scanning" if state.scanning_now else
              ("error" if state.consecutive_errors else "idle"),
        scanning=state.scanning_now, progress=state.scan_progress,
        total=state.scan_total, done=state.scan_done, last_error=state.last_error,
    )


@router.get("/{project_id}/stats", response_model=ProjectStats)
def project_stats(project_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    return _build_stats(db, project_id)


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project(project_id: int, payload: ProjectUpdate,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, eff = get_project_for_user(project_id, user, db, required_role=USER_ROLE_EDITOR)
    if eff not in (USER_ROLE_ADMIN, USER_ROLE_MANAGER):
        raise ForbiddenError("Only managers can edit project details.")
    if payload.name is not None:
        project.name = payload.name
    if payload.description is not None:
        project.description = payload.description
    if payload.color is not None:
        project.color = payload.color
    if payload.status is not None:
        project.status = payload.status
    db.commit()
    return _project_detail(db, project, user.id)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, request: Request,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, eff = get_project_for_user(project_id, user, db)
    if eff != USER_ROLE_ADMIN and user.role != USER_ROLE_ADMIN:
        if not (eff == USER_ROLE_MANAGER and project.created_by == user.id):
            raise ForbiddenError("Only the project creator or an admin can delete the project.")
    project.deleted_at = utcnow()
    record_audit(db, user=user, action="PROJECT_DELETED", project_id=project_id,
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()


# --- members ----------------------------------------------------------------

@router.get("/{project_id}/members", response_model=list[MemberOut])
def list_members(project_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    project, _m, eff = get_project_for_user(project_id, user, db)
    return _member_rows(db, project_id)


@router.get("/{project_id}/members/stats", response_model=list[MemberStat])
def member_stats(project_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Per-member contribution stats for a project (activities, uploads,
    comments, active locks, last seen)."""
    get_project_for_user(project_id, user, db)

    def _counts(col, *extra) -> dict:
        q = db.query(col, func.count(Activity.id)).filter(Activity.project_id == project_id)
        for cond in extra:
            q = q.filter(cond)
        return dict(q.group_by(col).all())

    activities = _counts(Activity.user_id)
    uploads = _counts(Activity.user_id, Activity.action == ACTION_UPLOAD_FILE)
    comments = dict(
        db.query(FileComment.user_id, func.count(FileComment.id))
        .filter(FileComment.project_id == project_id)
        .group_by(FileComment.user_id).all()
    )
    locks = dict(
        db.query(FileLock.locked_by_user_id, func.count(FileLock.id))
        .filter(FileLock.project_id == project_id, FileLock.released_at.is_(None))
        .group_by(FileLock.locked_by_user_id).all()
    )
    last_seen = dict(
        db.query(Activity.user_id, func.max(Activity.detected_at))
        .filter(Activity.project_id == project_id)
        .group_by(Activity.user_id).all()
    )

    out: list[MemberStat] = []
    for m in _member_rows(db, project_id):
        out.append(MemberStat(
            **m.model_dump(),
            activities=activities.get(m.user_id, 0),
            uploads=uploads.get(m.user_id, 0),
            comments=comments.get(m.user_id, 0),
            locks_active=locks.get(m.user_id, 0),
            last_activity_at=last_seen.get(m.user_id),
        ))
    out.sort(key=lambda s: (s.activities, s.comments), reverse=True)
    return out


@router.post("/{project_id}/members", response_model=list[MemberOut], status_code=201)
def add_member(project_id: int, payload: dict, request: Request,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, eff = get_project_for_user(project_id, user, db)
    if not _role_at_least(eff, USER_ROLE_MANAGER) and user.role != USER_ROLE_ADMIN:
        raise ForbiddenError("Only project managers can add members.")
    email = (payload.get("email") or "").strip().lower()
    role = payload.get("role", USER_ROLE_VIEWER)
    if not email:
        raise AppError("An email address is required.")
    target = db.query(User).filter(User.email == email).first()
    if target is None:
        raise NotFoundError("This user has not signed in to the application yet. "
                            "Ask them to log in once with Google first.")
    exists = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id, ProjectMember.user_id == target.id).first()
    if exists:
        raise ConflictError(f"{email} is already a member.")
    db.add(ProjectMember(project_id=project_id, user_id=target.id, role=role, added_by=user.id))
    record_audit(db, user=user, action="MEMBER_ADDED", project_id=project_id,
                 resource_type="USER", resource_id=str(target.id),
                 details={"email": email, "role": role}, ip=_ip(request),
                 user_agent=request.headers.get("user-agent"))
    db.commit()
    return _member_rows(db, project_id)


@router.patch("/{project_id}/members/{member_user_id}", response_model=list[MemberOut])
def update_member(project_id: int, member_user_id: int, payload: dict,
                  request: Request, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    project, _m, eff = get_project_for_user(project_id, user, db)
    if not _role_at_least(eff, USER_ROLE_MANAGER) and user.role != USER_ROLE_ADMIN:
        raise ForbiddenError("Only project managers can change member roles.")
    row = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == member_user_id).first()
    if not row:
        raise NotFoundError("Member not found.")
    new_role = payload.get("role", row.role)
    row.role = new_role
    record_audit(db, user=user, action="MEMBER_ROLE_CHANGED", project_id=project_id,
                 resource_type="USER", resource_id=str(member_user_id),
                 details={"role": new_role}, ip=_ip(request),
                 user_agent=request.headers.get("user-agent"))
    db.commit()
    return _member_rows(db, project_id)


@router.delete("/{project_id}/members/{member_user_id}", response_model=list[MemberOut],
               status_code=200)
def remove_member(project_id: int, member_user_id: int, request: Request,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, eff = get_project_for_user(project_id, user, db)
    if not _role_at_least(eff, USER_ROLE_MANAGER) and user.role != USER_ROLE_ADMIN:
        raise ForbiddenError("Only project managers can remove members.")
    row = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == member_user_id).first()
    if not row:
        raise NotFoundError("Member not found.")
    if row.user_id == project.created_by:
        raise ConflictError("The project creator cannot be removed.")
    db.delete(row)
    record_audit(db, user=user, action="MEMBER_REMOVED", project_id=project_id,
                 resource_type="USER", resource_id=str(member_user_id),
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    return _member_rows(db, project_id)


def _ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None