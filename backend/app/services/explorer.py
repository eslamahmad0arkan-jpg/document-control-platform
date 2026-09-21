"""Explorer: reads the synced snapshot to power the folder/file browser."""
from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models import File, FileLock, Folder, Project

_SORT_FIELDS = {
    "name": File.name,
    "size": File.size,
    "modified_time": File.modified_time,
    "extension": File.extension,
}

_FOLDER_MIME = "application/vnd.google-apps.folder"


def build_breadcrumbs(db: Session, project: Project, folder_drive_id: str | None) -> list[dict]:
    chain: list[dict] = []
    cur = folder_drive_id
    while cur and cur != "root":
        folder = db.query(Folder).filter(
            Folder.drive_id == cur, Folder.project_id == project.id
        ).first()
        if not folder:
            break
        chain.append({"drive_id": folder.drive_id, "name": folder.name})
        cur = folder.parent_folder_id
    chain.reverse()
    # The project root folder is never stored as a row, so it always leads the trail.
    if chain:
        chain.insert(0, {"drive_id": project.google_folder_id, "name": project.name})
    return chain


def node_detail(db: Session, project: Project, drive_id: str) -> dict | None:
    folder = db.query(Folder).filter(
        Folder.drive_id == drive_id, Folder.project_id == project.id
    ).first()
    if folder:
        return {
            "id": folder.id, "drive_id": folder.drive_id, "type": "FOLDER",
            "name": folder.name, "path": folder.path,
            "modified_time": folder.modified_time, "mime_type": "application/vnd.google-apps.folder",
            "size": None, "extension": None, "drive_url": None,
            "capabilities": None, "created_time": folder.created_time,
            "child_count": _child_count(db, project, folder.drive_id),
        }
    file_row = db.query(File).filter(
        File.drive_id == drive_id, File.project_id == project.id
    ).first()
    if file_row:
        return {
            "id": file_row.id, "drive_id": file_row.drive_id, "type": "FILE",
            "name": file_row.name, "path": file_row.path,
            "modified_time": file_row.modified_time, "mime_type": file_row.mime_type,
            "extension": file_row.extension, "size": file_row.size,
            "drive_url": file_row.drive_url, "capabilities": file_row.capabilities,
            "created_time": file_row.created_time,
            "modified_by_name": file_row.modified_by_name,
            "modified_by_email": file_row.modified_by_email,
            "child_count": 0,
        }
    return None


def _child_count(db: Session, project: Project, drive_id: str) -> int:
    subfolders = db.query(func.count(Folder.id)).filter(
        Folder.parent_folder_id == drive_id, Folder.project_id == project.id
    ).scalar() or 0
    subfiles = db.query(func.count(File.id)).filter(
        File.folder_drive_id == drive_id, File.project_id == project.id
    ).scalar() or 0
    return subfolders + subfiles


def explorer_listing(db: Session, project: Project, *, folder_drive_id: str | None = None,
                     q: str | None = None, ext: str | None = None,
                     sort: str = "name", order: str = "asc", page: int = 1,
                     per_page: int = 100) -> dict:
    drive_id = folder_drive_id if folder_drive_id and folder_drive_id != "root" else None
    current = None
    breadcrumbs: list[dict] = []
    if drive_id:
        current = node_detail(db, project, drive_id)
        breadcrumbs = build_breadcrumbs(db, project, drive_id) if current else []

    q_filter = None
    if q:
        like = f"%{q}%"
        q_filter = or_(File.name.ilike(like), File.path.ilike(like))

    # --- folders ------------------------------------------------------------
    folder_q = db.query(Folder).filter(
        Folder.project_id == project.id, Folder.trashed.is_(False)
    )
    if drive_id and not q:
        folder_q = folder_q.filter(Folder.parent_folder_id == drive_id)
    elif drive_id and q:
        folder_q = folder_q.filter(Folder.path.ilike(f"%{drive_id}%"))
    elif not drive_id and not q:
        # Root: show only top-level folders, not the whole project tree
        folder_q = folder_q.filter(
            or_(Folder.parent_folder_id.is_(None),
                Folder.parent_folder_id == project.google_folder_id))
    elif not drive_id and q:
        pass  # global search within project
    folders = folder_q.order_by(Folder.name.asc()).limit(per_page + 1).all()

    # --- files --------------------------------------------------------------
    file_q = db.query(File).filter(
        File.project_id == project.id, File.trashed.is_(False)
    )
    if drive_id and not q:
        file_q = file_q.filter(File.folder_drive_id == drive_id)
    elif drive_id and q:
        file_q = file_q.filter(File.path.ilike(f"%{drive_id}%"))
    elif not drive_id and not q:
        # Root: top-level files only, consistent with the root folder listing
        file_q = file_q.filter(
            or_(File.folder_drive_id.is_(None),
                File.folder_drive_id == project.google_folder_id))
    if q_filter is not None:
        file_q = file_q.filter(q_filter)
    if ext:
        file_q = file_q.filter(File.extension == ext.lstrip(".").lower())

    sort_col = _SORT_FIELDS.get(sort, File.name)
    order_cls = sort_col.asc() if order == "asc" else sort_col.desc()
    count = file_q.count()
    files_rows = file_q.order_by(order_cls).offset((page - 1) * per_page).limit(per_page).all()
    total_folders = folder_q.count()

    # Per-folder direct counts so cards/rows show "N files · M subfolders" cheaply.
    folder_ids = [f.drive_id for f in folders]
    subfolders_by_parent = dict(
        db.query(Folder.parent_folder_id, func.count(Folder.id))
        .filter(Folder.parent_folder_id.in_(folder_ids), Folder.trashed.is_(False))
        .group_by(Folder.parent_folder_id).all()
    )
    files_by_parent = dict(
        db.query(File.folder_drive_id, func.count(File.id))
        .filter(File.folder_drive_id.in_(folder_ids), File.trashed.is_(False))
        .group_by(File.folder_drive_id).all()
    )

    base_fields = [
        "id", "name", "path", "mime_type", "modified_time",
        "size", "extension", "drive_url", "capabilities", "created_time",
        "modified_by_name", "modified_by_email",
    ]

    file_ids = [f.drive_id for f in files_rows]
    locks_by_drive = {
        lock.drive_id: lock
        for lock in db.query(FileLock).filter(
            FileLock.drive_id.in_(file_ids),
            FileLock.released_at.is_(None),
        ).all()
    }

    def _lock_payload(lock: FileLock | None):
        if lock is None:
            return None
        u = lock.locked_by
        return {
            "id": lock.id,
            "drive_id": lock.drive_id,
            "file_id": lock.file_id,
            "locked_by_user_id": lock.locked_by_user_id,
            "locked_by_name": u.name if u else "Unknown",
            "locked_by_email": u.email if u else "",
            "comment": lock.comment,
            "created_at": lock.created_at,
            "released_at": lock.released_at,
            "released_by_user_id": lock.released_by_user_id,
        }

    folder_nodes = [
        {
            **{k: getattr(f, k, None) for k in base_fields},
            "type": "FOLDER",
            "drive_id": f.drive_id,
            "parent_folder_id": f.parent_folder_id,
            "folder_drive_id": None,
            "mime_type": _FOLDER_MIME,
            "child_count": _child_count(db, project, f.drive_id),
            "child_files": files_by_parent.get(f.drive_id, 0),
            "child_folders": subfolders_by_parent.get(f.drive_id, 0),
        }
        for f in folders
    ]
    file_nodes = [
        {
            "id": f.id, "drive_id": f.drive_id, "type": "FILE", "name": f.name,
            "mime_type": f.mime_type, "extension": f.extension, "size": f.size, "path": f.path,
            "drive_url": f.drive_url, "modified_time": f.modified_time,
            "modified_by_name": f.modified_by_name, "modified_by_email": f.modified_by_email,
            "created_time": f.created_time, "capabilities": f.capabilities, "child_count": 0,
            "parent_folder_id": None, "folder_drive_id": f.folder_drive_id,
            "lock": _lock_payload(locks_by_drive.get(f.drive_id)),
        }
        for f in files_rows
    ]
    return {
        "project_id": project.id,
        "current": current,
        "breadcrumbs": breadcrumbs,
        "folders": folder_nodes,
        "files": file_nodes,
        "total_folders": total_folders,
        "total_files": count,
        "search_active": bool(q) or bool(ext),
    }


def search_project(db: Session, project: Project, q: str, *,
                   limit_files: int = 25, limit_folders: int = 25,
                   limit_activities: int = 25) -> list[dict]:
    like = f"%{q}%"
    folders = (
        db.query(Folder)
        .filter(Folder.project_id == project.id, Folder.trashed.is_(False),
                Folder.name.ilike(like))
        .order_by(Folder.name.asc()).limit(limit_folders).all()
    )
    files = (
        db.query(File)
        .filter(File.project_id == project.id, File.trashed.is_(False),
                or_(File.name.ilike(like), File.extension.ilike(like)))
        .order_by(File.modified_time.desc().nullslast())
        .limit(limit_files).all()
    )
    from ..models import Activity

    activities = (
        db.query(Activity)
        .filter(Activity.project_id == project.id,
                Activity.target_name.ilike(like))
        .order_by(Activity.detected_at.desc())
        .limit(limit_activities).all()
    )
    results: list[dict] = []
    for f in folders:
        results.append({
            "drive_id": f.drive_id, "type": "FOLDER", "name": f.name, "path": f.path,
            "extension": None, "modified_time": f.modified_time, "drive_url": None,
        })
    for f in files:
        results.append({
            "drive_id": f.drive_id, "type": "FILE", "name": f.name, "path": f.path,
            "extension": f.extension, "modified_time": f.modified_time,
            "drive_url": f.drive_url,
        })
    for a in activities:
        results.append({
            "id": a.id, "type": "ACTIVITY", "action": a.action,
            "target_name": a.target_name, "target_type": a.target_type,
            "detected_at": a.detected_at, "folder_path": a.folder_path,
        })
    return results[:120]