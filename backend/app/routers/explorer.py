"""Folder/file explorer + Drive mutations.

All mutations are performed on the ORIGINAL Google Drive via the acting user's
own token — Google enforces the user's real permissions (403 contact is surfaced
cleanly). The local snapshot is updated afterwards so monitoring stays consistent.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File as FastAPIFile, Request, Response, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_project_for_user
from ..errors import (
    AppError, ForbiddenError, GoogleApiError, GoogleNotFoundError, NotFoundError,
)
from ..models import (
    ACTIVITY_SOURCE_APP,
    ACTION_CREATED,
    ACTION_CREATE_FOLDER,
    ACTION_MOVED,
    ACTION_RENAMED,
    ACTION_TRASHED,
    ACTION_UNTRASHED,
    ACTION_UPLOAD_FILE,
    File, Folder, Project, SyncState, User, USER_ROLE_EDITOR, USER_ROLE_MANAGER, utcnow,
)
from ..schemas import (CreateFolderRequest, ExplorerResponse,
                       MoveRequest, RenameRequest, TrashRequest)
from ..services.audit_service import record_audit
from ..services.explorer import explorer_listing, node_detail
from ..services.google.drive import DriveClient, FOLDER_MIME
from ..services.sync import (
    build_drive_client, pick_sync_identity, _upsert_file, _upsert_folder,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["explorer"])


def _can_edit(_role: str) -> None:
    if not (_role in (USER_ROLE_EDITOR, USER_ROLE_MANAGER)):
        raise ForbiddenError("Your project role only allows read-only access.")


def _ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)


def _record_app_activity(db: Session, project: Project, user: User, action: str,
                         target_type: str, name: str, *, drive_id: str,
                         file_id: int | None = None, folder_id: int | None = None,
                         path: str | None = None, folder_path: str | None = None,
                         details: dict | None = None, exclude_user_id: int | None = None):
    from ..services.activity import record_activity

    record_activity(
        db, project, user, action, target_type, name,
        drive_id=drive_id, file_id=file_id, folder_id=folder_id,
        file_path=path, folder_path=folder_path,
        actor_email=user.email, actor_name=user.name,
        details=details, source=ACTIVITY_SOURCE_APP,
        notify_extra={"exclude_user_id": exclude_user_id},
    )


@router.get("/explorer", response_model=ExplorerResponse)
def list_explorer(project_id: int, folder_id: str | None = None, q: str | None = None,
                  ext: str | None = None, sort: str = "name", order: str = "asc",
                  page: int = 1, per_page: int = 100,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    return explorer_listing(
        db, project, folder_drive_id=folder_id, q=q, ext=ext,
        sort=sort, order=order, page=page, per_page=min(per_page, 500),
    )


@router.get("/explorer/item/{drive_id}")
def item_detail(project_id: int, drive_id: str,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    node = node_detail(db, project, drive_id)
    if node is None:
        raise NotFoundError("Item not found.")
    return node


@router.get("/files/{drive_id}/content")
def file_content(project_id: int, drive_id: str, download: bool = False,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Streams the file bytes to the browser through the project's approved Drive
    account, so members can view files without logging into Google each time.
    Google-native docs (Docs/Sheets/Slides) are exported to PDF for inline preview."""
    project, _m, _r = get_project_for_user(project_id, user, db)
    row = db.query(File).filter(
        File.drive_id == drive_id, File.project_id == project.id
    ).first()
    if row is None:
        raise NotFoundError("File not found.")
    try:
        _syncer, drive = pick_sync_identity(db, project, user)
    except GoogleApiError as exc:
        raise AppError(f"Drive access unavailable: {getattr(exc, 'message', exc)}")
    mime = row.mime_type or "application/octet-stream"
    export_mime = "application/pdf" if mime.startswith("application/vnd.google-apps.") else None
    try:
        content, content_type = drive.get_media(row.drive_id, export_mime)
    except GoogleApiError as exc:
        raise AppError(f"Unable to load file: {getattr(exc, 'message', exc)}")
    if not content:
        raise AppError("Google returned an empty file.")
    quoted = quote(row.name)
    disposition = "attachment" if download else "inline"
    return Response(
        content=content,
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quoted}"},
    )


def _is_project_root(drive_id: str | None, project: Project) -> bool:
    return bool(drive_id) and (drive_id == project.google_folder_id
                               or drive_id == "root")


@router.post("/folders", status_code=201)
def create_folder(project_id: int, payload: CreateFolderRequest, request: Request,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    drive = build_drive_client(db, user)
    parent = payload.parent_id if payload.parent_id and payload.parent_id != "root" else None
    if parent and not _is_project_root(parent, project):
        node = node_detail(db, project, parent)
        if node is None:
            raise NotFoundError("Parent folder not found.")
    try:
        meta = drive.create_folder(parent, payload.name)
    except Exception as exc:
        raise AppError(f"Google Drive refused: {getattr(exc, 'message', exc)}")
    parent_row = db.query(Folder).filter(Folder.drive_id == parent).first() if parent else None
    parent_path = parent_row.path if parent_row else ""
    depth = parent_row.depth + 1 if parent_row else 1
    folder, _created = _upsert_folder(db, project, meta, parent_path, depth,
                                      parent_row.id if parent_row else None)
    _record_app_activity(db, project, user, ACTION_CREATE_FOLDER, "FOLDER",
                         folder.name, drive_id=folder.drive_id, folder_id=folder.id,
                         folder_path=folder.path, path=folder.path)
    record_audit(db, user=user, action="CREATE_FOLDER", project_id=project_id,
                 resource_type="FOLDER", resource_id=meta.get("id"),
                 details={"name": payload.name, "parent": parent}, ip=_ip(request),
                 user_agent=request.headers.get("user-agent"))
    db.commit()
    return node_detail(db, project, meta["id"])


@router.post("/upload", status_code=201)
async def upload_file(project_id: int, folder_id: str | None = None,
                      file: UploadFile = FastAPIFile(...), request: Request = None,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    parent = folder_id if folder_id and folder_id != "root" else None
    if parent and not _is_project_root(parent, project):
        node = node_detail(db, project, parent)
        if node is None:
            raise NotFoundError("Parent folder not found.")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise AppError("File exceeds the 20 MB upload limit for this endpoint.")
    drive = build_drive_client(db, user)
    try:
        meta = drive.upload_file(parent or project.google_folder_id,
                                 file.filename or "unnamed",
                                 content, file.content_type or "application/octet-stream")
    except Exception as exc:
        raise AppError(f"Google Drive refused upload: {getattr(exc, 'message', exc)}")
    parent_row = db.query(Folder).filter(Folder.drive_id == parent).first() if parent else None
    parent_path = parent_row.path if parent_row else ""
    row, _created, _diff = _upsert_file(db, project, meta, parent_path,
                                        parent_row.id if parent_row else None)
    _record_app_activity(db, project, user, ACTION_UPLOAD_FILE, "FILE", row.name,
                         drive_id=row.drive_id, file_id=row.id, folder_id=row.db_folder_id,
                         path=row.path, folder_path=parent_path,
                         details={"size": row.size, "ext": row.extension})
    record_audit(db, user=user, action="UPLOAD_FILE", project_id=project_id,
                 resource_type="FILE", resource_id=meta.get("id"),
                 details={"name": file.filename, "parent": parent},
                 ip=_ip(request),
                 user_agent=request.headers.get("user-agent") if request else None)
    db.commit()
    return node_detail(db, project, meta["id"])


@router.post("/rename")
def rename_item(project_id: int, payload: RenameRequest, request: Request,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    drive = build_drive_client(db, user)
    try:
        meta = drive.rename(payload.drive_id, payload.name)
    except GoogleNotFoundError:
        raise NotFoundError("Item not found in Google Drive.")
    except Exception as exc:
        raise AppError(f"Google Drive refused rename: {getattr(exc, 'message', exc)}")
    node = node_detail(db, project, payload.drive_id) or {}
    is_folder = node.get("type") == "FOLDER"
    if is_folder:
        folder = db.query(Folder).filter(Folder.drive_id == payload.drive_id).first()
        if folder:
            old_name = folder.name
            folder.name = payload.name
            folder.path = _rebuild_path(db, folder)
            _record_app_activity(db, project, user, ACTION_RENAMED, "FOLDER",
                                 folder.name, drive_id=folder.drive_id,
                                 folder_id=folder.id, path=folder.path,
                                 folder_path=_parent_path(db, folder),
                                 details={"old": old_name})
    else:
        row = db.query(File).filter(File.drive_id == payload.drive_id).first()
        if row:
            old_name = row.name
            row.name = payload.name
            row.path = _rebuild_file_path(db, row)
            _record_app_activity(db, project, user, ACTION_RENAMED, "FILE",
                                 row.name, drive_id=row.drive_id, file_id=row.id,
                                 path=row.path, folder_path=_parent_path_of(row.path),
                                 details={"old": old_name})
    record_audit(db, user=user, action="RENAME", project_id=project_id,
                 resource_type="ITEM", resource_id=payload.drive_id,
                 details={"new_name": payload.name}, ip=_ip(request),
                 user_agent=request.headers.get("user-agent"))
    db.commit()
    return node_detail(db, project, payload.drive_id)


@router.post("/move")
def move_item(project_id: int, payload: MoveRequest, request: Request,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    node = node_detail(db, project, payload.drive_id)
    if node is None:
        raise NotFoundError("Item not found.")
    drive = build_drive_client(db, user)
    new_parent = payload.parent_id if payload.parent_id and payload.parent_id != "root" else None
    try:
        drive.move(payload.drive_id, new_parent)
    except GoogleNotFoundError:
        raise NotFoundError("Item not found in Google Drive.")
    except Exception as exc:
        raise AppError(f"Google Drive refused move: {getattr(exc, 'message', exc)}")
    is_folder = node.get("type") == "FOLDER"
    new_parent_row = db.query(Folder).filter(Folder.drive_id == new_parent).first()
    if is_folder:
        folder = db.query(Folder).filter(Folder.drive_id == payload.drive_id).first()
        if folder:
            folder.parent_folder_id = new_parent
            folder.db_parent_id = new_parent_row.id if new_parent_row else None
            folder.path = _rebuild_path(db, folder)
            folder.depth = (new_parent_row.depth + 1) if new_parent_row else 1
            _record_app_activity(db, project, user, ACTION_MOVED, "FOLDER",
                                 folder.name, drive_id=folder.drive_id,
                                 folder_id=folder.id, path=folder.path,
                                 folder_path=_parent_path(db, folder))
    else:
        row = db.query(File).filter(File.drive_id == payload.drive_id).first()
        if row:
            row.folder_drive_id = new_parent
            row.db_folder_id = new_parent_row.id if new_parent_row else None
            row.path = _rebuild_file_path(db, row)
            _record_app_activity(db, project, user, ACTION_MOVED, "FILE",
                                 row.name, drive_id=row.drive_id, file_id=row.id,
                                 path=row.path, folder_path=_parent_path_of(row.path))
    record_audit(db, user=user, action="MOVE", project_id=project_id,
                 resource_type="ITEM", resource_id=payload.drive_id,
                 details={"new_parent": new_parent}, ip=_ip(request),
                 user_agent=request.headers.get("user-agent"))
    db.commit()
    return node_detail(db, project, payload.drive_id)


@router.post("/trash")
def trash_item(project_id: int, payload: TrashRequest, request: Request,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    node = node_detail(db, project, payload.drive_id)
    if node is None:
        raise NotFoundError("Item not found.")
    drive = build_drive_client(db, user)
    try:
        drive.set_trashed(payload.drive_id, True)
    except GoogleNotFoundError:
        raise NotFoundError("Item not found in Google Drive.")
    except Exception as exc:
        raise AppError(f"Google Drive refused: {getattr(exc, 'message', exc)}")
    is_folder = node.get("type") == "FOLDER"
    if is_folder:
        folder = db.query(Folder).filter(Folder.drive_id == payload.drive_id).first()
        if folder:
            folder.trashed = True
            _record_app_activity(db, project, user, ACTION_TRASHED, "FOLDER",
                                 folder.name, drive_id=folder.drive_id,
                                 folder_id=folder.id, path=folder.path,
                                 folder_path=_parent_path(db, folder))
    else:
        row = db.query(File).filter(File.drive_id == payload.drive_id).first()
        if row:
            row.trashed = True
            _record_app_activity(db, project, user, ACTION_TRASHED, "FILE",
                                 row.name, drive_id=row.drive_id, file_id=row.id,
                                 path=row.path, folder_path=_parent_path_of(row.path))
    record_audit(db, user=user, action="TRASH", project_id=project_id,
                 resource_type="ITEM", resource_id=payload.drive_id,
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    return {"ok": True, "trashed": payload.drive_id}


@router.get("/trash")
def list_trash(project_id: int, per_page: int = 200,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Recycle-bin view: recently trashed folders & files in this project."""
    project, _m, _role = get_project_for_user(project_id, user, db)
    folders = (
        db.query(Folder)
        .filter(Folder.project_id == project.id, Folder.trashed.is_(True))
        .order_by(Folder.last_synced_at.desc())
        .limit(per_page)
        .all()
    )
    files = (
        db.query(File)
        .filter(File.project_id == project.id, File.trashed.is_(True))
        .order_by(File.last_synced_at.desc())
        .limit(per_page)
        .all()
    )
    return {
        "folders": [
            {"drive_id": f.drive_id, "name": f.name, "path": f.path,
             "trashed_at": f.last_synced_at}
            for f in folders
        ],
        "files": [
            {"drive_id": f.drive_id, "name": f.name, "path": f.path,
             "size": f.size, "extension": f.extension, "trashed_at": f.last_synced_at}
            for f in files
        ],
    }


@router.post("/files/{drive_id}/restore")
def restore_item(project_id: int, drive_id: str, request: Request,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Restore a trashed item in Google Drive and clear its local flag."""
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    folder = db.query(Folder).filter(Folder.drive_id == drive_id,
                                     Folder.project_id == project.id).first()
    row = db.query(File).filter(File.drive_id == drive_id,
                                File.project_id == project.id).first()
    if folder is None and row is None:
        raise NotFoundError("Item not found.")
    drive = build_drive_client(db, user)
    try:
        drive.set_trashed(drive_id, False)
    except GoogleNotFoundError:
        raise NotFoundError("Item not found in Google Drive.") from None
    except Exception as exc:
        raise AppError(f"Google Drive refused: {getattr(exc, 'message', exc)}") from None
    if folder:
        folder.trashed = False
        _record_app_activity(db, project, user, ACTION_UNTRASHED, "FOLDER",
                             folder.name, drive_id=folder.drive_id,
                             folder_id=folder.id, path=folder.path,
                             folder_path=_parent_path(db, folder))
    else:
        row.trashed = False
        _record_app_activity(db, project, user, ACTION_UNTRASHED, "FILE",
                             row.name, drive_id=row.drive_id, file_id=row.id,
                             path=row.path, folder_path=_parent_path_of(row.path))
    record_audit(db, user=user, action="RESTORE", project_id=project_id,
                 resource_type="ITEM", resource_id=drive_id,
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    return {"ok": True, "restored": drive_id}


# --- path rebuilding helpers --------------------------------------------------

def _rebuild_path(db: Session, folder: Folder) -> str:
    if not folder.parent_folder_id:
        return folder.name
    parent = db.query(Folder).filter(Folder.drive_id == folder.parent_folder_id).first()
    if parent:
        return f"{parent.path}/{folder.name}"
    return folder.name


def _parent_path(db: Session, folder: Folder) -> str:
    if not folder.parent_folder_id:
        return ""
    parent = db.query(Folder).filter(Folder.drive_id == folder.parent_folder_id).first()
    return parent.path if parent else ""


def _rebuild_file_path(db: Session, row: File) -> str:
    if not row.folder_drive_id:
        return row.name
    parent = db.query(Folder).filter(Folder.drive_id == row.folder_drive_id).first()
    if parent:
        return f"{parent.path}/{row.name}"
    return row.name


def _parent_path_of(path: str) -> str:
    if not path or "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]