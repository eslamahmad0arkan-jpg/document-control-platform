"""Synchronization engine.

Google Drive feed realities reflected here:
- `changes.list` is incremental; we persist the page cursor so nothing is lost on restart.
- Change records carry fileId + removed flag only; action type is inferred by diffing
  our snapshot (CREATED / MODIFIED / MOVED / RENAMED / TRASHED / UNTRASHED).
- Modified-by is taken from `lastModifyingUser` when provided by Google.
- If a folder changed, its subtree is re-discovered so children appear correctly.
- API failures keep the previous cursor (retry later) => no missed activity.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..errors import GoogleApiError, GoogleAuthExpiredError, GoogleNotFoundError
from ..models import (
    ACTIVITY_SOURCE_GOOGLE,
    ACTIVITY_SOURCE_MANUAL,
    ACTION_CREATED,
    ACTION_MODIFIED,
    ACTION_MOVED,
    ACTION_RENAMED,
    ACTION_TRASHED,
    ACTION_UNTRASHED,
    File,
    Folder,
    Project,
    ProjectMember,
    SyncState,
    User,
    utcnow,
)
from .activity import record_activity
from .google.drive import DriveClient, FOLDER_MIME, SHORTCUT_MIME
from .google.oauth import get_valid_access_token, refresh_access_token

_MAX_ANCESTOR_DEPTH = 40
_FOLDER_MIME = FOLDER_MIME
_COMMIT_EVERY = 200  # batch commit size for large change feeds


def _normalize_meta(meta: dict) -> dict:
    """Resolve Drive shortcuts (`application/vnd.google-apps.shortcut`) to their
    targets so a shortcut behaves exactly like the folder/file it points to.
    A folder-targeted shortcut is stored as that folder (its children are then
    discovered); a file-targeted shortcut is stored as its target file."""
    details = meta.get("shortcutDetails") or {}
    target_id = details.get("targetId")
    if not target_id:
        return meta
    out = dict(meta)
    out["id"] = target_id
    out["mimeType"] = details.get("targetMimeType") or meta.get("mimeType")
    return out


def build_drive_client(db: Session, user: User) -> DriveClient:
    token = get_valid_access_token(db, user)
    return DriveClient(token, refresh_cb=lambda: refresh_access_token(db, user))


def pick_sync_identity(db: Session, project: Project, user: User | None = None) -> tuple[User, DriveClient]:
    """Pick a project member whose Google token can actually read the project root.
    Prefers the creator/owner, then falls back to other members."""
    candidates: list[User] = []
    if user is not None:
        candidates.append(user)
    members = (
        db.query(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .filter(ProjectMember.project_id == project.id, User.is_active.is_(True))
        .order_by(ProjectMember.created_at.asc())
        .all()
    )
    seen: set[int] = set()
    ordered: list[User] = []
    for u in candidates + members:
        if u.id not in seen:
            seen.add(u.id)
            ordered.append(u)

    errors: list[str] = []
    for cand in ordered:
        try:
            client = build_drive_client(db, cand)
            client.get_file(project.google_folder_id)
            return cand, client
        except (GoogleApiError, GoogleAuthExpiredError, ValueError) as exc:
            errors.append(f"{cand.email}: {getattr(exc, 'message', str(exc))}")
            continue
    raise GoogleAuthExpiredError(
        details={"needReconnect": True, "attempts": errors},
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt
    except ValueError:
        return None


def _extract_ext(name: str, mime: str) -> str:
    if "." in name:
        return name.rsplit(".", 1)[1].lower()
    generic = mime.split(";")[0].split("/")
    if generic and generic[0] == "application" and len(generic) > 1:
        return generic[1].lower()
    return ""


# --------------------------------------------------------------------------
# Upsert helpers
# --------------------------------------------------------------------------

def _upsert_folder(db: Session, project: Project, meta: dict,
                   parent_path: str, depth: int, db_parent_id: int | None) -> tuple[Folder, bool]:
    drive_id = meta["id"]
    folder = db.query(Folder).filter(Folder.drive_id == drive_id).first()
    name = meta.get("name", "")
    path = f"{parent_path}/{name}" if parent_path else name
    created = folder is None
    if folder is None:
        folder = Folder(
            drive_id=drive_id, project_id=project.id, name=name, path=path,
            parent_folder_id=_first_parent(meta), db_parent_id=db_parent_id,
            depth=depth,
            created_time=_parse_time(meta.get("createdTime")),
            modified_time=_parse_time(meta.get("modifiedTime")),
            trashed=bool(meta.get("trashed", False)),
        )
        db.add(folder)
    else:
        folder.name = name
        folder.path = path
        folder.parent_folder_id = _first_parent(meta)
        folder.db_parent_id = db_parent_id
        folder.depth = depth
        folder.modified_time = _parse_time(meta.get("modifiedTime")) or folder.modified_time
        folder.trashed = bool(meta.get("trashed", False))
    folder.last_synced_at = utcnow()
    db.flush()
    return folder, created


def _upsert_file(db: Session, project: Project, meta: dict,
                 folder_path: str, db_folder_id: int | None) -> tuple[File, bool, dict]:
    drive_id = meta["id"]
    existing = db.query(File).filter(File.drive_id == drive_id).first()
    name = meta.get("name", "")
    path = f"{folder_path}/{name}" if folder_path else name
    mime = meta.get("mimeType", "")
    ext = _extract_ext(name, mime)
    size = int(meta.get("size") or 0) if meta.get("size") is not None else 0
    modified = _parse_time(meta.get("modifiedTime"))
    trashed = bool(meta.get("trashed", False))
    capabilities = meta.get("capabilities") or None
    mu = meta.get("lastModifyingUser") or {}
    new_checksum = hashlib.sha256(
        f"{drive_id}:{name}:{size}:{modified}:{trashed}".encode()
    ).hexdigest()

    diff: dict = {}
    if existing is None:
        row = File(
            drive_id=drive_id, project_id=project.id, name=name, path=path,
            mime_type=mime, extension=ext, size=size,
            folder_drive_id=_first_parent(meta), db_folder_id=db_folder_id,
            drive_url=meta.get("webViewLink"),
            created_time=_parse_time(meta.get("createdTime")),
            modified_time=modified, trashed=trashed, capabilities=capabilities,
            modified_by_email=mu.get("emailAddress"), modified_by_name=mu.get("displayName"),
            checksum=new_checksum,
        )
        db.add(row)
        db.flush()
        return row, True, diff

    if existing.name != name:
        diff["name_old"] = existing.name
    if existing.checksum != new_checksum:
        diff["modified"] = True
    old_parent = existing.folder_drive_id
    new_parent = _first_parent(meta)
    if old_parent != new_parent:
        diff["parents"] = (old_parent, new_parent)
    if existing.trashed != trashed:
        diff["trashed"] = (existing.trashed, trashed)

    existing.name = name
    existing.path = path
    existing.mime_type = mime
    existing.extension = ext
    existing.folder_drive_id = new_parent
    existing.db_folder_id = db_folder_id
    existing.size = size
    existing.drive_url = meta.get("webViewLink") or existing.drive_url
    existing.created_time = _parse_time(meta.get("createdTime")) or existing.created_time
    existing.modified_time = modified or existing.modified_time
    existing.modified_by_email = mu.get("emailAddress") or existing.modified_by_email
    existing.modified_by_name = mu.get("displayName") or existing.modified_by_name
    existing.trashed = trashed
    existing.capabilities = capabilities or existing.capabilities
    existing.checksum = new_checksum
    existing.last_synced_at = utcnow()
    db.flush()
    return existing, False, diff


def _first_parent(meta: dict) -> str | None:
    parents = meta.get("parents")
    return parents[0] if parents else None


def _ancestor_in_scope(drive: DriveClient, file_meta: dict, root_id: str) -> bool:
    """Check whether a file belongs under the project root by walking parents."""
    current = file_meta
    for _ in range(_MAX_ANCESTOR_DEPTH):
        parents = current.get("parents") or []
        if not parents:
            return False
        parent = parents[0]
        if parent == root_id:
            return True
        current = drive.get_file(parent)
    return False


# --------------------------------------------------------------------------
# Full scan
# --------------------------------------------------------------------------

class ScanCancelled(Exception):
    pass


def full_scan(db: Session, project: Project, user: User, *,
              record_activities: bool = False,
              progress_cb: Callable[[dict], None] | None = None) -> dict:
    """Breadth-first snapshot of the project root into folders/files tables."""
    _, drive = pick_sync_identity(db, project, user)
    state = _get_or_create_state(db, project)
    state.scanning_now = True
    state.scan_progress = 0
    state.scan_done = 0
    state.scan_total = 0
    state.last_error = None
    # Leftover shortcut placeholders are superseded by their resolved targets.
    db.query(File).filter(
        File.project_id == project.id, File.mime_type == SHORTCUT_MIME
    ).delete(synchronize_session=False)
    db.commit()

    root_id = project.google_folder_id
    root_meta = drive.get_file(root_id)
    root_name = root_meta.get("name", project.name)
    if project.name == root_id and root_name:
        project.name = root_name
    db.flush()

    folders_count = 0
    files_count = 0
    # drive_id -> (Folder row, path) cache
    folder_cache: dict[str, tuple[Folder, str]] = {root_id: (None, "")}
    queue = [root_id]
    done = 0
    pending = 1

    def _report():
        if progress_cb:
            progress_cb({
                "phase": "scan",
                "scanning": True,
                "progress": int((done / max(pending + done, 1)) * 100),
                "done": done,
                "total": pending + done,
                "folders": folders_count,
                "files": files_count,
            })

    try:
        while queue:
            current = queue.pop(0)
            if settings.SYNC_DELAY_BETWEEN_REQUESTS > 0:
                time.sleep(settings.SYNC_DELAY_BETWEEN_REQUESTS)
            _, current_path = folder_cache.get(current, (None, ""))
            children = drive.list_children(current, trashed=True)
            for child in children:
                child = _normalize_meta(child)
                child_id = child["id"]
                if child.get("mimeType") == _FOLDER_MIME:
                    parent_db, _ = folder_cache.get(current, (None, ""))
                    db_parent_id = parent_db.id if parent_db else None
                    chain = current_path
                    depth = 0 if not chain else chain.count("/") + 1
                    folder, created = _upsert_folder(db, project, child, chain, depth, db_parent_id)
                    folder_cache[child_id] = (folder, folder.path)
                    if created:
                        folders_count += 1
                    queue.append(child_id)
                else:
                    parent_db, parent_path = folder_cache.get(current, (None, ""))
                    db_folder_id = parent_db.id if parent_db else None
                    file_row, is_new, _diff = _upsert_file(db, project, child, parent_path, db_folder_id)
                    if is_new:
                        files_count += 1
                        if record_activities:
                            record_activity(
                                db, project, user, ACTION_CREATED, "FILE", file_row.name,
                                drive_id=file_row.drive_id, file_id=file_row.id,
                                folder_id=db_folder_id, file_path=file_row.path,
                                folder_path=parent_path,
                                actor_email=None, actor_name=None,
                                details={"ext": file_row.extension, "size": file_row.size},
                                source=ACTIVITY_SOURCE_MANUAL,
                            )
                done += 1
                state.scan_done = done
                state.scan_total = queue.__len__() + done
                state.scan_progress = int((done / max(state.scan_total, 1)) * 100)
                _report()
                # Flush progress in batches for very large roots.
                if done % _COMMIT_EVERY == 0:
                    db.commit()
    finally:
        state.scanning_now = False
        state.scan_progress = 100
        state.scan_done = done
        state.scan_total = done
        state.last_full_scan_at = utcnow()
        try:
            db.commit()
        except Exception:
            db.rollback()
            db.add(state)
            db.commit()
        if progress_cb:
            progress_cb({
                "phase": "done", "scanning": False, "progress": 100,
                "done": done, "total": done,
                "folders": folders_count, "files": files_count,
            })
    return {"folders": folders_count, "files": files_count}


# --------------------------------------------------------------------------
# Incremental sync via Google change feed
# --------------------------------------------------------------------------

def _get_or_create_state(db: Session, project: Project) -> SyncState:
    state = db.query(SyncState).filter(SyncState.project_id == project.id).first()
    if state is None:
        state = SyncState(project_id=project.id)
        db.add(state)
        db.flush()
    return state


def _mark_sync_error(state: SyncState, exc: Exception) -> None:
    state.last_error = f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"
    state.consecutive_errors = (state.consecutive_errors or 0) + 1
    state.scanning_now = False


def incremental_sync(db: Session, project: Project, user: User) -> dict:
    """Pull the Drive change feed since the last cursor, diff against snapshot,
    record activities, and persist the new cursor atomically."""
    _, drive = pick_sync_identity(db, project, user)
    state = _get_or_create_state(db, project)
    state.scanning_now = True
    db.commit()

    activities_recorded = 0
    items_seen = 0

    try:
        cursor = state.last_cursor or state.start_page_token
        if not cursor:
            token = drive.get_start_page_token()
            state.start_page_token = token
            db.commit()
            cursor = token
        # Pull change pages since the cursor and persist the new cursor atomically.
        items_seen, activities_recorded, new_start = _process_change_pages(
            db, drive, project, state, cursor, user
        )
        state.start_page_token = new_start or state.start_page_token
        state.last_cursor = None
        state.last_error = None
        state.consecutive_errors = 0
        state.last_incremental_at = utcnow()
        state.scanning_now = False
        db.commit()
    except Exception as exc:  # keep old cursor; nothing is lost
        db.rollback()
        state = _get_or_create_state(db, project)
        _mark_sync_error(state, exc)
        db.commit()
        raise

    return {"activities": activities_recorded, "items_seen": items_seen,
            "cursor": new_start}


def _process_change_pages(db: Session, drive: DriveClient, project: Project,
                          state: SyncState, cursor: str, user: User) -> tuple[int, int, str]:
    seen = 0
    recorded = 0
    new_start = cursor
    pending = 0

    while True:
        data = drive.list_changes(cursor)
        changes = data.get("changes", [])
        for change in changes:
            seen += 1
            if _apply_change(db, drive, project, state, change, user):
                recorded += 1
            pending += 1
            # Commit in batches so a huge feed does not hold one giant
            # transaction (keeps memory flat and lets the UI see progress).
            if pending >= _COMMIT_EVERY:
                db.commit()
                pending = 0
        cursor = data.get("nextPageToken")
        if data.get("newStartPageToken"):
            new_start = data["newStartPageToken"]
        if data.get("nextStartPageToken"):
            new_start = data["nextStartPageToken"]
        if not cursor:
            break
        if settings.SYNC_DELAY_BETWEEN_REQUESTS > 0:
            time.sleep(settings.SYNC_DELAY_BETWEEN_REQUESTS)
    return seen, recorded, new_start


def _apply_change(db: Session, drive: DriveClient, project: Project,
                  state: SyncState, change: dict, actor: User) -> bool:
    file_id = change.get("fileId")
    removed = bool(change.get("removed", False))
    if not file_id:
        return False

    if removed:
        return _handle_removed(db, project, file_id)

    try:
        meta = drive.get_file(file_id)
    except GoogleNotFoundError:
        return _handle_removed(db, project, file_id)

    if not meta.get("id"):
        return False

    # Shortcuts resolve to their targets (folder shortcuts become the folder,
    # file shortcuts become the file), so the rest of the pipeline sees only
    # real nodes.
    meta = _normalize_meta(meta)

    # Scope check driven by parents walk (cheap path first — lookup existing record)
    is_folder = meta.get("mimeType") == _FOLDER_MIME

    if is_folder:
        return _apply_folder_change(db, drive, project, meta, actor)
    return _apply_file_change(db, drive, project, meta, actor)


def _handle_removed(db: Session, project: Project, drive_id: str) -> bool:
    """Item disappeared from the user's drive feed: mark trashed and record."""
    folder = db.query(Folder).filter(Folder.drive_id == drive_id).first()
    file_row = db.query(File).filter(File.drive_id == drive_id).first()
    recorded = False
    if folder is not None and not folder.trashed:
        folder.trashed = True
        record_activity(db, project, None, ACTION_TRASHED, "FOLDER",
                        folder.name, drive_id=drive_id, folder_id=folder.id,
                        folder_path=folder.path, source=ACTIVITY_SOURCE_GOOGLE)
        recorded = True
    if file_row is not None and not file_row.trashed:
        file_row.trashed = True
        record_activity(db, project, None, ACTION_TRASHED, "FILE",
                        file_row.name, drive_id=drive_id, file_id=file_row.id,
                        folder_id=file_row.db_folder_id, file_path=file_row.path,
                        folder_path=_parent_path(file_row.path), source=ACTIVITY_SOURCE_GOOGLE)
        recorded = True
    return recorded


def _parent_path(path: str) -> str:
    if not path or "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


def _apply_file_change(db: Session, drive: DriveClient, project: Project,
                       meta: dict, actor: User) -> bool:
    drive_id = meta["id"]
    existing = db.query(File).filter(File.drive_id == drive_id).first()
    parent_id = _first_parent(meta)

    if existing is None:
        if parent_id is None or not _ancestor_in_scope(drive, meta, project.google_folder_id):
            return False
        parent_path = _folder_path_for(db, project, parent_id)
        row, _created, _diff = _upsert_file(db, project, meta, parent_path,
                                            _db_folder_id(db, parent_id))
        mu = meta.get("lastModifyingUser") or {}
        record_activity(
            db, project, actor, ACTION_CREATED, "FILE", row.name,
            drive_id=drive_id, file_id=row.id, folder_id=row.db_folder_id,
            file_path=row.path, folder_path=parent_path,
            actor_email=mu.get("emailAddress"), actor_name=mu.get("displayName"),
            details={"ext": row.extension, "size": row.size},
            source=ACTIVITY_SOURCE_GOOGLE,
        )
        return True

    row, _created, diff = _upsert_file(db, project, meta, _folder_path_for(db, project, parent_id),
                                       _db_folder_id(db, parent_id))
    mu = meta.get("lastModifyingUser") or {}
    recorded = False
    if diff.get("trashed"):
        now_trashed = diff["trashed"][1]
        action = ACTION_TRASHED if now_trashed else ACTION_UNTRASHED
        record_activity(db, project, actor, action, "FILE", row.name,
                        drive_id=drive_id, file_id=row.id, folder_id=row.db_folder_id,
                        file_path=row.path, folder_path=_parent_path(row.path),
                        actor_email=mu.get("emailAddress"), actor_name=mu.get("displayName"),
                        source=ACTIVITY_SOURCE_GOOGLE)
        recorded = True
        _apply_removed_children_if_trashed(db, project, drive_id, action == ACTION_TRASHED)
    if diff.get("name_old"):
        record_activity(db, project, actor, ACTION_RENAMED, "FILE", row.name,
                        drive_id=drive_id, file_id=row.id, folder_id=row.db_folder_id,
                        file_path=row.path, folder_path=_parent_path(row.path),
                        actor_email=mu.get("emailAddress"), actor_name=mu.get("displayName"),
                        details={"old": diff["name_old"]}, source=ACTIVITY_SOURCE_GOOGLE)
        recorded = True
    if diff.get("parents"):
        old_parent, new_parent = diff["parents"]
        if old_parent and new_parent and old_parent != new_parent:
            record_activity(db, project, actor, ACTION_MOVED, "FILE", row.name,
                            drive_id=drive_id, file_id=row.id, folder_id=row.db_folder_id,
                            file_path=row.path, folder_path=_parent_path(row.path),
                            actor_email=mu.get("emailAddress"), actor_name=mu.get("displayName"),
                            details={"from": _folder_name(db, project, old_parent),
                                     "to": _folder_name(db, project, new_parent)},
                            source=ACTIVITY_SOURCE_GOOGLE)
            recorded = True
    if diff.get("modified") and not diff.get("trashed"):
        record_activity(db, project, actor, ACTION_MODIFIED, "FILE", row.name,
                        drive_id=drive_id, file_id=row.id, folder_id=row.db_folder_id,
                        file_path=row.path, folder_path=_parent_path(row.path),
                        actor_email=mu.get("emailAddress"), actor_name=mu.get("displayName"),
                        details={"size": row.size, "ext": row.extension},
                        source=ACTIVITY_SOURCE_GOOGLE)
        recorded = True
    return recorded


def _apply_folder_change(db: Session, drive: DriveClient, project: Project,
                         meta: dict, actor: User) -> bool:
    drive_id = meta["id"]
    existing = db.query(Folder).filter(Folder.drive_id == drive_id).first()
    parent_id = _first_parent(meta)

    if existing is None:
        if parent_id is None or not _ancestor_in_scope(drive, meta, project.google_folder_id):
            return False
        parent_path = _folder_path_for(db, project, parent_id)
        parent_db = _db_folder_id(db, parent_id)
        depth = parent_path.count("/") + 1 if parent_path else 1
        row, _created = _upsert_folder(db, project, meta, parent_path, depth, parent_db)
        record_activity(db, project, actor, ACTION_CREATED, "FOLDER", row.name,
                        drive_id=drive_id, folder_id=row.id, folder_path=row.path,
                        source=ACTIVITY_SOURCE_GOOGLE)
        _sweep_folder_children(db, drive, project, row)
        return True

    row, _created = _upsert_folder(db, project, meta, _folder_path_for(db, project, parent_id),
                                   _depth_for(db, project, parent_id),
                                   _db_folder_id(db, parent_id) or existing.db_parent_id)
    recorded = False
    if existing.name != row.name:
        record_activity(db, project, actor, ACTION_RENAMED, "FOLDER", row.name,
                        drive_id=drive_id, folder_id=row.id, folder_path=row.path,
                        details={"old": existing.name}, source=ACTIVITY_SOURCE_GOOGLE)
        recorded = True
    if existing.parent_folder_id != row.parent_folder_id:
        record_activity(db, project, actor, ACTION_MOVED, "FOLDER", row.name,
                        drive_id=drive_id, folder_id=row.id, folder_path=row.path,
                        details={"from": _folder_name(db, project, existing.parent_folder_id),
                                 "to": _folder_name(db, project, row.parent_folder_id)},
                        source=ACTIVITY_SOURCE_GOOGLE)
        recorded = True
    if existing.trashed != row.trashed:
        action = ACTION_TRASHED if row.trashed else ACTION_UNTRASHED
        record_activity(db, project, actor, action, "FOLDER", row.name,
                        drive_id=drive_id, folder_id=row.id, folder_path=row.path,
                        source=ACTIVITY_SOURCE_GOOGLE)
        recorded = True
        _apply_removed_children_if_trashed(db, project, drive_id, row.trashed)

    if recorded:
        # Re-discover subtree so paths and children stay consistent.
        _sweep_folder_children(db, drive, project, row)
    return recorded


def _sweep_folder_children(db: Session, drive: DriveClient, project: Project, folder: Folder) -> None:
    """Re-list a folder's children and upsert; records CREATED for genuinely new items."""
    try:
        children = drive.list_children(folder.drive_id, trashed=True)
    except GoogleApiError:
        return
    for child in children:
        child = _normalize_meta(child)
        if child.get("mimeType") == _FOLDER_MIME:
            _upsert_folder(db, project, child, folder.path, folder.depth + 1, folder.id)
        else:
            row, is_new, _d = _upsert_file(db, project, child, folder.path, folder.id)
            if is_new:
                record_activity(db, project, None, ACTION_CREATED, "FILE", row.name,
                                drive_id=row.drive_id, file_id=row.id, folder_id=folder.id,
                                file_path=row.path, folder_path=folder.path,
                                actor_email=row.modified_by_email, actor_name=row.modified_by_name,
                                details={"ext": row.extension, "size": row.size},
                                source=ACTIVITY_SOURCE_GOOGLE)


def _apply_removed_children_if_trashed(db: Session, project: Project,
                                       drive_id: str, trashed: bool) -> None:
    """Mark descendants trashed when a folder is trashed/untrashed."""
    for child_folder in db.query(Folder).filter(Folder.parent_folder_id == drive_id).all():
        _apply_removed_children_if_trashed(db, project, child_folder.drive_id, trashed)
        child_folder.trashed = trashed
    for child_file in db.query(File).filter(File.folder_drive_id == drive_id).all():
        child_file.trashed = trashed


# --------------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------------

def _db_folder_id(db: Session, drive_id: str | None) -> int | None:
    if not drive_id:
        return None
    folder = db.query(Folder).filter(Folder.drive_id == drive_id).first()
    return folder.id if folder else None


def _folder_path_for(db: Session, project: Project, drive_id: str | None) -> str:
    if not drive_id:
        return ""
    folder = db.query(Folder).filter(Folder.drive_id == drive_id).first()
    if folder and folder.project_id == project.id:
        return folder.path
    return ""


def _folder_name(db: Session, project: Project, drive_id: str | None) -> str:
    if not drive_id:
        return "?"
    folder = db.query(Folder).filter(Folder.drive_id == drive_id).first()
    return folder.name if folder else drive_id


def _depth_for(db: Session, project: Project, drive_id: str | None) -> int:
    """Depth for a folder given its Drive parent id. Children of the project root
    (or of a not-yet-synced parent) live at depth 1."""
    if not drive_id or drive_id == project.google_folder_id:
        return 1
    folder = db.query(Folder).filter(Folder.drive_id == drive_id).first()
    return (folder.depth + 1) if folder else 1