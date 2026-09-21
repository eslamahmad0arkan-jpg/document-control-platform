"""Project structure: an unlimited-depth WBS / document tree per project.

Sections (أقسام) ← branches (فروع) ← extensions (امتداد) ← ... form a pure
metadata tree layered ON TOP of the Google Drive snapshot. Each node can link
real Drive items (files or folders) and tag them with document-control metadata:
Document Index number (doc_number), WBS code, revision and status.
Google Drive stays the source of truth — nothing here mutates Drive.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_current_user, get_project_for_user
from ..errors import ConflictError, NotFoundError
from ..models import (
    ACTION_STRUCTURE_CREATED,
    ACTION_STRUCTURE_DELETED,
    ACTION_STRUCTURE_RENAMED,
    ACTION_LINKED,
    ACTION_UNLINKED,
    File,
    Folder,
    Project,
    ProjectStructureLink,
    ProjectStructureNode,
    STRUCTURE_STATUSES,
    SYNC_TARGET_TYPE_FILE,
    SYNC_TARGET_TYPE_FOLDER,
    User,
    utcnow,
)
from ..schemas import (
    StructureLinkCreate,
    StructureLinkOut,
    StructureLinkUpdate,
    StructureNodeCreate,
    StructureNodeDetail,
    StructureNodeOut,
    StructureNodeUpdate,
    StructureTree,
)
from ..services.audit_service import record_audit
from .explorer import _can_edit, _ip, _record_app_activity

router = APIRouter(prefix="/api/projects/{project_id}/structure", tags=["structure"])


# --- helpers -----------------------------------------------------------------

def _load_node(db: Session, project: Project, node_id: int) -> ProjectStructureNode:
    node = (
        db.query(ProjectStructureNode)
        .filter(ProjectStructureNode.id == node_id,
                ProjectStructureNode.project_id == project.id)
        .first()
    )
    if node is None:
        raise NotFoundError("Structure node not found.")
    return node


def _snapshot_maps(db: Session, project: Project, drive_ids: list[str]):
    files = (
        db.query(File.drive_id, File.name, File.size, File.extension,
                 File.mime_type, File.modified_time)
        .filter(File.project_id == project.id, File.drive_id.in_(drive_ids))
        .all()
    )
    folders = (
        db.query(Folder.drive_id, Folder.name, Folder.modified_time)
        .filter(Folder.project_id == project.id, Folder.drive_id.in_(drive_ids))
        .all()
    )
    fm = {r.drive_id: r for r in files}
    fm_extra = {}
    for r in files:
        fm_extra[r.drive_id] = {
            "size": r.size, "extension": r.extension,
            "mime_type": r.mime_type, "modified_time": r.modified_time,
        }
    fdm = {r.drive_id: r for r in folders}
    fdm_extra = {r.drive_id: {"modified_time": r.modified_time} for r in folders}
    return fm_extra, fdm_extra, (files or []) is not None


def _link_out(db: Session, project: Project, link: ProjectStructureLink) -> dict:
    """Serialize a link, merging the latest Drive snapshot fields if present."""
    fm_extra, fdm_extra, _ = _snapshot_maps(db, project, [link.drive_id])
    extra = fm_extra.get(link.drive_id)
    if extra is None:
        extra = {"size": None, "extension": None, "mime_type": None,
                 "modified_time": None}
    if link.target_type == SYNC_TARGET_TYPE_FOLDER and link.drive_id in fdm_extra:
        extra["modified_time"] = fdm_extra[link.drive_id]["modified_time"]
    return {
        "id": link.id,
        "project_id": link.project_id,
        "node_id": link.node_id,
        "drive_id": link.drive_id,
        "target_type": link.target_type,
        "item_name": link.item_name,
        "doc_number": link.doc_number,
        "wbs_code": link.wbs_code,
        "revision": link.revision,
        "status": link.status,
        "description": link.description,
        "position": link.position,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
        "available": (link.drive_id in fm_extra) or
                     (link.target_type == SYNC_TARGET_TYPE_FOLDER and link.drive_id in fdm_extra),
        "size": extra["size"],
        "extension": extra["extension"],
        "mime_type": extra["mime_type"],
        "modified_time": extra["modified_time"],
    }


def _node_out(node: ProjectStructureNode) -> dict:
    return {
        "id": node.id, "project_id": node.project_id, "parent_id": node.parent_id,
        "name": node.name, "code": node.code, "description": node.description,
        "position": node.position, "created_at": node.created_at,
        "updated_at": node.updated_at,
    }


def _build_tree(nodes: list[ProjectStructureNode], roots: list[ProjectStructureNode]) -> list[dict]:
    by_parent: dict[int | None, list[ProjectStructureNode]] = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)
    for lst in by_parent.values():
        lst.sort(key=lambda x: (x.position, x.id))

    def build(parent_id: int | None) -> list:
        out = []
        for n in by_parent.get(parent_id, []):
            node = _node_out(n)
            node["children"] = build(n.id)
            out.append(node)
        return out

    return build(None)


def _descendants_count(nodes: list[ProjectStructureNode], node_id: int) -> int:
    child_map: dict[int | None, list[int]] = {}
    for n in nodes:
        child_map.setdefault(n.parent_id, []).append(n.id)
    count = 0
    stack = list(child_map.get(node_id, []))
    while stack:
        count += 1
        stack.extend(child_map.get(stack.pop(), []))
    return count


def _direct_children(nodes: list[ProjectStructureNode], node_id: int) -> list[int]:
    return sorted((n.id for n in nodes if n.parent_id == node_id))


# --- nodes -------------------------------------------------------------------

@router.get("/tree", response_model=list[StructureTree])
def structure_tree(project_id: int,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    nodes = (
        db.query(ProjectStructureNode)
        .filter(ProjectStructureNode.project_id == project.id)
        .order_by(ProjectStructureNode.position, ProjectStructureNode.id)
        .all()
    )
    roots = [n for n in nodes if n.parent_id is None]
    return _build_tree(nodes, roots)


@router.get("/nodes/{node_id}", response_model=StructureNodeDetail)
def structure_node_detail(project_id: int, node_id: int, include_links: bool = True,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    node = _load_node(db, project, node_id)
    all_nodes = (
        db.query(ProjectStructureNode.id)
        .filter(ProjectStructureNode.project_id == project.id)
        .all()
    )
    node_ids = [r.id for r in all_nodes]
    out = _node_out(node)
    out["descendants"] = node_ids.count(node.parent_id)  # placeholder, replaced below
    # compute properly with all children rows
    nodes = (
        db.query(ProjectStructureNode)
        .filter(ProjectStructureNode.project_id == project.id)
        .all()
    )
    out["descendants"] = _descendants_count(nodes, node_id)
    out["children"] = _direct_children(nodes, node_id)
    out["links"] = []
    if include_links:
        links = (
            db.query(ProjectStructureLink)
            .filter(ProjectStructureLink.node_id == node_id)
            .order_by(ProjectStructureLink.position, ProjectStructureLink.id)
            .all()
        )
        out["links"] = [_link_out(db, project, lk) for lk in links]
    return out


@router.get("/nodes", response_model=list[StructureNodeOut])
def structure_root_nodes(project_id: int,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    nodes = (
        db.query(ProjectStructureNode)
        .filter(ProjectStructureNode.project_id == project.id,
                ProjectStructureNode.parent_id.is_(None))
        .order_by(ProjectStructureNode.position, ProjectStructureNode.id)
        .all()
    )
    return [_node_out(n) for n in nodes]


@router.post("/nodes", response_model=StructureNodeOut, status_code=201)
def create_structure_node(project_id: int, payload: StructureNodeCreate, request: Request,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    parent = None
    if payload.parent_id is not None:
        parent = _load_node(db, project, payload.parent_id)
    if payload.code:
        existing = (
            db.query(ProjectStructureNode)
            .filter(ProjectStructureNode.project_id == project.id,
                    ProjectStructureNode.code == payload.code)
            .first()
        )
        if existing:
            raise ConflictError(f"Code '{payload.code}' is already in use.")
    siblings = (
        db.query(ProjectStructureNode.position)
        .filter(ProjectStructureNode.project_id == project.id,
                ProjectStructureNode.parent_id == payload.parent_id)
        .count()
    )
    node = ProjectStructureNode(
        project_id=project.id,
        parent_id=payload.parent_id,
        name=payload.name.strip(),
        code=(payload.code or "").strip() or None,
        description=payload.description,
        position=payload.position if payload.position is not None else siblings,
        created_by=user.id,
    )
    db.add(node)
    db.flush()
    _record_app_activity(
        db, project, user, ACTION_STRUCTURE_CREATED, "STRUCTURE", node.name,
        drive_id="", path="", folder_path="",
        details={"node_id": node.id, "parent_id": node.parent_id, "code": node.code},
        exclude_user_id=user.id,
    )
    record_audit(db, user=user, action="STRUCTURE_CREATE", project_id=project_id,
                 resource_type="STRUCTURE", resource_id=str(node.id),
                 details={"name": node.name, "parent_id": node.parent_id,
                          "code": node.code, "parent_name": parent.name if parent else None},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    db.refresh(node)
    return _node_out(node)


@router.patch("/nodes/{node_id}", response_model=StructureNodeOut)
def update_structure_node(project_id: int, node_id: int, payload: StructureNodeUpdate,
                          request: Request, user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    node = _load_node(db, project, node_id)
    old_name = node.name
    if payload.name is not None:
        node.name = payload.name.strip()
    if payload.code is not None:
        node.code = (payload.code or "").strip() or None
        existing = (
            db.query(ProjectStructureNode)
            .filter(ProjectStructureNode.project_id == project.id,
                    ProjectStructureNode.code == node.code,
                    ProjectStructureNode.id != node.id)
            .first()
        )
        if existing:
            raise ConflictError(f"Code '{node.code}' is already in use.")
    if payload.description is not None:
        node.description = payload.description
    if payload.position is not None:
        node.position = payload.position
    db.flush()
    if payload.name is not None and payload.name.strip() != old_name:
        _record_app_activity(
            db, project, user, ACTION_STRUCTURE_RENAMED, "STRUCTURE", node.name,
            drive_id="", path="", folder_path="",
            details={"node_id": node.id, "old_name": old_name, "code": node.code},
            exclude_user_id=user.id,
        )
    record_audit(db, user=user, action="STRUCTURE_UPDATE", project_id=project_id,
                 resource_type="STRUCTURE", resource_id=str(node.id),
                 details={"name": node.name, "code": node.code,
                          "description": node.description, "position": node.position},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    db.refresh(node)
    return _node_out(node)


@router.delete("/nodes/{node_id}", status_code=204)
def delete_structure_node(project_id: int, node_id: int, request: Request,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    node = _load_node(db, project, node_id)
    # walk the subtree: project-wide (id, parent_id) rows -> child_map -> stack
    rows = (
        db.query(ProjectStructureNode.id, ProjectStructureNode.parent_id)
        .filter(ProjectStructureNode.project_id == project.id)
        .all()
    )
    cm = child_map(rows)
    ids_to_delete = [node_id]
    seen = {node_id}
    stack = list(cm.get(node_id, []))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        ids_to_delete.append(cur)
        stack.extend(cm.get(cur, []))
    # delete links first, then the nodes (links cascade via delete-orphan)
    db.query(ProjectStructureLink).filter(
        ProjectStructureLink.node_id.in_(ids_to_delete)
    ).delete(synchronize_session=False)
    db.query(ProjectStructureNode).filter(
        ProjectStructureNode.id.in_(ids_to_delete)
    ).delete(synchronize_session=False)
    _record_app_activity(
        db, project, user, ACTION_STRUCTURE_DELETED, "STRUCTURE", node.name,
        drive_id="", path="", folder_path="",
        details={"node_id": node.id, "node_count": len(ids_to_delete)},
        exclude_user_id=user.id,
    )
    record_audit(db, user=user, action="STRUCTURE_DELETE", project_id=project_id,
                 resource_type="STRUCTURE", resource_id=str(node.id),
                 details={"name": node.name, "node_count": len(ids_to_delete)},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
@router.post("/nodes/{node_id}/links", response_model=StructureLinkOut, status_code=201)
def link_drive_item(project_id: int, node_id: int, payload: StructureLinkCreate,
                    request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    _load_node(db, project, node_id)
    if payload.status not in STRUCTURE_STATUSES:
        raise NotFoundError(f"Unknown document status '{payload.status}'.")
    existing = (
        db.query(ProjectStructureLink)
        .filter(ProjectStructureLink.node_id == node_id,
                ProjectStructureLink.drive_id == payload.drive_id)
        .first()
    )
    if existing:
        raise ConflictError("This Drive item is already linked to this node.")
    # snapshot name at link time
    item_name = payload.drive_id
    if payload.target_type == SYNC_TARGET_TYPE_FOLDER:
        f = db.query(Folder).filter(Folder.project_id == project.id,
                                    Folder.drive_id == payload.drive_id).first()
        if f:
            item_name = f.name
    else:
        f = db.query(File).filter(File.project_id == project.id,
                                  File.drive_id == payload.drive_id).first()
        if f:
            item_name = f.name
    link = ProjectStructureLink(
        project_id=project.id, node_id=node_id, drive_id=payload.drive_id,
        target_type=payload.target_type, item_name=item_name,
        doc_number=(payload.doc_number or "").strip() or None,
        wbs_code=(payload.wbs_code or "").strip() or None,
        revision=(payload.revision or "").strip() or None,
        status=payload.status,
        description=payload.description,
        position=position_for_link(db, node_id),
        created_by=user.id,
    )
    db.add(link)
    db.flush()
    _record_app_activity(
        db, project, user, ACTION_LINKED, "STRUCTURE_LINK", item_name,
        drive_id=payload.drive_id, path="", folder_path="",
        details={"node_id": node_id, "target_type": payload.target_type,
                 "doc_number": link.doc_number, "status": link.status},
        exclude_user_id=user.id,
    )
    record_audit(db, user=user, action="STRUCTURE_LINK", project_id=project_id,
                 resource_type="STRUCTURE", resource_id=str(node_id),
                 details={"drive_id": payload.drive_id, "item_name": item_name,
                          "doc_number": link.doc_number, "status": link.status,
                          "revision": link.revision},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    db.refresh(link)
    return _link_out(db, project, link)


@router.patch("/links/{link_id}", response_model=StructureLinkOut)
def update_structure_link(project_id: int, link_id: int, payload: StructureLinkUpdate,
                          request: Request, user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    link = (
        db.query(ProjectStructureLink)
        .filter(ProjectStructureLink.id == link_id,
                ProjectStructureLink.project_id == project.id)
        .first()
    )
    if link is None:
        raise NotFoundError("Link not found.")
    if payload.status is not None and payload.status not in STRUCTURE_STATUSES:
        raise NotFoundError(f"Unknown document status '{payload.status}'.")
    if payload.doc_number is not None:
        link.doc_number = (payload.doc_number or "").strip() or None
    if payload.wbs_code is not None:
        link.wbs_code = (payload.wbs_code or "").strip() or None
    if payload.revision is not None:
        link.revision = (payload.revision or "").strip() or None
    if payload.status is not None:
        link.status = payload.status
    if payload.description is not None:
        link.description = payload.description
    if payload.position is not None:
        link.position = payload.position
    record_audit(db, user=user, action="STRUCTURE_LINK_UPDATE", project_id=project_id,
                 resource_type="STRUCTURE", resource_id=str(link.node_id),
                 details={"link_id": link.id, "drive_id": link.drive_id,
                          "doc_number": link.doc_number, "wbs_code": link.wbs_code,
                          "revision": link.revision, "status": link.status},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    db.refresh(link)
    return _link_out(db, project, link)


@router.delete("/links/{link_id}", status_code=204)
def unlink_drive_item(project_id: int, link_id: int, request: Request,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    link = (
        db.query(ProjectStructureLink)
        .filter(ProjectStructureLink.id == link_id,
                ProjectStructureLink.project_id == project.id)
        .first()
    )
    if link is None:
        raise NotFoundError("Link not found.")
    _record_app_activity(
        db, project, user, ACTION_UNLINKED, "STRUCTURE_LINK", link.item_name,
        drive_id=link.drive_id, path="", folder_path="",
        details={"node_id": link.node_id, "link_id": link.id},
        exclude_user_id=user.id,
    )
    record_audit(db, user=user, action="STRUCTURE_UNLINK", project_id=project_id,
                 resource_type="STRUCTURE", resource_id=str(link.node_id),
                 details={"link_id": link.id, "drive_id": link.drive_id},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.delete(link)
    db.commit()


# --- helper utility ----------------------------------------------------------

def child_map(nodes: list[tuple[int, int | None]]) -> dict[int | None, list[int]]:
    """Map parent_id -> [child ids] from project-wide (id, parent_id) rows."""
    cm: dict[int | None, list[int]] = {}
    for nid, pid in nodes:
        cm.setdefault(pid, []).append(nid)
    for lst in cm.values():
        lst.sort()
    return cm


def position_for_link(db: Session, node_id: int) -> int:
    return (
        db.query(ProjectStructureLink.position)
        .filter(ProjectStructureLink.node_id == node_id)
        .count()
    )


def _is_descendant(child_map_: dict[int | None, list[int]], node_id: int) -> bool:
    """Return True if node_id appears as a parent (i.e. has children) in the map."""
    return node_id in child_map_
