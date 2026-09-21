"""Project search across folders, files, and activities."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_project_for_user
from ..errors import AppError
from ..models import File, Folder, Project, ProjectMember, User, USER_ROLE_ADMIN
from ..schemas import SearchResponse
from ..services.explorer import search_project

router = APIRouter(prefix="/api/projects/{project_id}/search", tags=["search"])


@router.get("", response_model=SearchResponse)
def search(project_id: int, q: str = Query(..., min_length=1, max_length=120),
           limit: int = Query(50, ge=1, le=200),
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    items = search_project(db, project, q, limit_files=limit, limit_folders=limit,
                           limit_activities=limit)
    return SearchResponse(items=items, total=len(items), q=q)


global_router = APIRouter(prefix="/api/search", tags=["search"])


@global_router.get("")
def global_search(q: str = Query("", max_length=120),
                  limit: int = Query(40, ge=1, le=150),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One command-palette query across every project the caller can see.
    Returns projects, then matching folders, then matching files."""
    q = q.strip()
    if not q:
        return {"items": [], "total": 0, "q": q}

    project_ids = [
        pid for (pid,) in db.query(ProjectMember.project_id)
        .filter(ProjectMember.user_id == user.id).distinct().all()
    ]
    if user.role == USER_ROLE_ADMIN:
        project_ids += [
            pid for (pid,) in db.query(Project.id)
            .filter(Project.deleted_at.is_(None)).all()
        ]
    project_ids = list(dict.fromkeys(project_ids))
    if not project_ids:
        return {"items": [], "total": 0, "q": q}

    projects = {
        p.id: p for p in db.query(Project).filter(
            Project.id.in_(project_ids), Project.deleted_at.is_(None)).all()
    }
    like = f"%{q}%"
    items: list[dict] = []

    for p in projects.values():
        if q.lower() in (p.name or "").lower():
            items.append({
                "kind": "PROJECT", "project_id": p.id, "project_name": p.name,
                "name": p.name, "path": "", "drive_id": p.google_folder_id,
                "extension": None, "mime_type": None, "size": None,
                "modified_time": p.created_at,
            })

    files = (
        db.query(File)
        .filter(File.project_id.in_(list(projects)), File.trashed.is_(False),
                or_(File.name.ilike(like), File.path.ilike(like)))
        .order_by(File.name.asc())
        .limit(limit)
        .all()
    )
    for f in files:
        p = projects.get(f.project_id)
        if not p:
            continue
        items.append({
            "kind": "FILE", "project_id": p.id, "project_name": p.name,
            "name": f.name, "path": f.path, "drive_id": f.drive_id,
            "extension": f.extension, "mime_type": f.mime_type, "size": f.size,
            "modified_time": f.modified_time, "drive_url": f.drive_url,
        })

    folders = (
        db.query(Folder)
        .filter(Folder.project_id.in_(list(projects)), Folder.trashed.is_(False),
                or_(Folder.name.ilike(like), Folder.path.ilike(like)))
        .order_by(Folder.name.asc())
        .limit(limit)
        .all()
    )
    for fo in folders:
        p = projects.get(fo.project_id)
        if not p:
            continue
        items.append({
            "kind": "FOLDER", "project_id": p.id, "project_name": p.name,
            "name": fo.name, "path": fo.path, "drive_id": fo.drive_id,
            "extension": None, "mime_type": "application/vnd.google-apps.folder",
            "size": None, "modified_time": fo.modified_time, "drive_url": None,
        })

    # cap: projects first, then folders, then files — keep the palette snappy
    kind_rank = {"PROJECT": 0, "FOLDER": 1, "FILE": 2}
    items.sort(key=lambda it: (kind_rank.get(it["kind"], 3), it["name"] or ""))
    items = items[:150]
    return {"items": items, "total": len(items), "q": q}