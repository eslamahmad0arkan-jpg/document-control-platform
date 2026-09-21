"""Document-control features: file lock (check-out/check-in) and comments.

Locks are advisory metadata on top of the Drive snapshot — Drive itself stays
the source of truth, but a lock prevents team members from missing conflicts.
Comments attach to a file (by drive_id) and fan out to project members as
notifications (respecting each member's per-project preference).
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi import File as FastAPIFile
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import DATA_DIR, settings
from ..database import get_db
from ..deps import get_current_user, get_project_for_user
from ..errors import AppError, ConflictError, ForbiddenError, NotFoundError
from ..models import (
    ACTION_COMMENTED,
    ACTION_LOCKED,
    ACTION_UNLOCKED,
    File,
    FileApproval,
    FileComment,
    FileCommentAttachment,
    FileLock,
    Notification,
    NotificationPref,
    Project,
    ProjectMember,
    User,
    USER_ROLE_MANAGER,
    utcnow,
)
from ..schemas import (
    CommentAttachmentOut,
    CommentCreate,
    CommentOut,
    FileApprovalCreate,
    FileApprovalDecision,
    FileApprovalOut,
    FileLockOut,
    LockRequest,
)
from ..services.audit_service import record_audit
from .explorer import _can_edit, _ip, _record_app_activity

router = APIRouter(prefix="/api/projects/{project_id}", tags=["document-control"])


def _load_file(db: Session, project: Project, drive_id: str) -> File:
    row = db.query(File).filter(
        File.drive_id == drive_id, File.project_id == project.id
    ).first()
    if row is None:
        raise NotFoundError("File not found.")
    return row


def _comment_activity(db: Session, project: Project, user: User, row: File,
                      body: str, reply_to: int | None = None,
                      mention_ids: list[int] | None = None):
    """Record a file-comment activity (fan-out notification to members)."""
    _record_app_activity(
        db, project, user, ACTION_COMMENTED, "FILE", row.name,
        drive_id=row.drive_id, file_id=row.id, path=row.path,
        folder_path=row.path.rsplit("/", 1)[0] if "/" in row.path else "",
        details={"comment": (body or "")[:200], "reply_to": reply_to,
                 "mentions": mention_ids or []},
        exclude_user_id=user.id,
    )


def _lock_out(lock: FileLock) -> dict:
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


def _active_lock(db: Session, project: Project, drive_id: str) -> FileLock | None:
    return (
        db.query(FileLock)
        .filter(
            FileLock.project_id == project.id,
            FileLock.drive_id == drive_id,
            FileLock.released_at.is_(None),
        )
        .order_by(FileLock.created_at.desc())
        .first()
    )


# --- locks ------------------------------------------------------------------

@router.get("/files/{drive_id}/lock", response_model=FileLockOut | None)
def get_lock(project_id: int, drive_id: str,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    lock = _active_lock(db, project, drive_id)
    return _lock_out(lock) if lock else None


@router.post("/files/{drive_id}/lock", response_model=FileLockOut)
def lock_file(project_id: int, drive_id: str, payload: LockRequest, request: Request,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    _can_edit(role)
    row = _load_file(db, project, drive_id)
    existing = _active_lock(db, project, drive_id)
    if existing and not payload.force:
        # A manager may take over the lock without down-grading permissions.
        raise ConflictError(
            "This file is locked for editing.",
            details={
                "locked_by": existing.locked_by.email if existing.locked_by else "",
                "created_at": existing.created_at.isoformat(),
            },
        )
    if existing and existing.locked_by_user_id != user.id and existing.released_at is None:
        existing.released_at = utcnow()
        existing.released_by_user_id = user.id
    lock = FileLock(
        project_id=project.id, drive_id=drive_id, file_id=row.id,
        locked_by_user_id=user.id, comment=payload.comment,
    )
    db.add(lock)
    _record_app_activity(
        db, project, user, ACTION_LOCKED, "FILE", row.name,
        drive_id=drive_id, file_id=row.id, path=row.path,
        folder_path=row.path.rsplit("/", 1)[0] if "/" in row.path else "",
        details={"comment": payload.comment},
        exclude_user_id=user.id,
    )
    record_audit(db, user=user, action="LOCK_FILE", project_id=project_id,
                 resource_type="FILE", resource_id=drive_id,
                 details={"name": row.name, "comment": payload.comment,
                          "force": payload.force},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    lock = db.query(FileLock).filter(FileLock.id == lock.id).first()
    return _lock_out(lock)


@router.post("/files/{drive_id}/unlock", response_model=FileLockOut)
def unlock_file(project_id: int, drive_id: str, request: Request,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    lock = _active_lock(db, project, drive_id)
    if lock is None:
        raise NotFoundError("This file is not locked.")
    row = db.query(File).filter(File.drive_id == drive_id,
                                File.project_id == project.id).first()
    can_release = (
        lock.locked_by_user_id == user.id
        or role == USER_ROLE_MANAGER
        or user.role == "ADMIN"
    )
    if not can_release:
        raise ForbiddenError("Only the member who locked the file (or a manager) can release it.")
    lock.released_at = utcnow()
    lock.released_by_user_id = user.id
    if row:
        _record_app_activity(
            db, project, user, ACTION_UNLOCKED, "FILE", row.name,
            drive_id=drive_id, file_id=row.id, path=row.path,
            folder_path=row.path.rsplit("/", 1)[0] if "/" in row.path else "",
            details={"by": user.email}, exclude_user_id=user.id,
        )
    record_audit(db, user=user, action="UNLOCK_FILE", project_id=project_id,
                 resource_type="FILE", resource_id=drive_id,
                 details={"name": row.name if row else "?"},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    return _lock_out(lock)


@router.get("/locks", response_model=list[FileLockOut])
def list_locks(project_id: int, state: str = "active",
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List active (default) or recent locks for the project."""
    project, _m, _role = get_project_for_user(project_id, user, db)
    q = db.query(FileLock).filter(FileLock.project_id == project.id)
    if state == "active":
        q = q.filter(FileLock.released_at.is_(None))
    else:
        q = q.filter(FileLock.released_at.is_not(None))
    rows = q.order_by(FileLock.created_at.desc()).limit(200).all()
    return [_lock_out(r) for r in rows]


# --- comments ---------------------------------------------------------------

def _attach_out(db: Session, cid: int) -> list[dict]:
    rows = (
        db.query(FileCommentAttachment)
        .filter(FileCommentAttachment.comment_id == cid)
        .order_by(FileCommentAttachment.created_at.asc())
        .all()
    )
    return [
        {
            "id": a.id,
            "filename": a.filename,
            "size": a.size,
            "content_type": a.content_type,
            "download_url": f"/api/projects/{a.project_id}/comments/{cid}/attachments/{a.id}/download",
            "created_at": a.created_at,
        }
        for a in rows
    ]


def _comment_out(db: Session, c: FileComment, with_replies: bool = True) -> dict:
    u = c.user
    reply_count = (
        db.query(func.count(FileComment.id))
        .filter(FileComment.parent_id == c.id)
        .scalar()
    )
    out = {
        "id": c.id,
        "drive_id": c.drive_id,
        "file_id": c.file_id,
        "parent_id": c.parent_id,
        "user_id": c.user_id,
        "user_name": u.name if u else "Unknown",
        "user_email": u.email if u else "",
        "body": c.body,
        "mentions": c.mentions or [],
        "attachments": _attach_out(db, c.id),
        "reply_count": reply_count or 0,
        "created_at": c.created_at,
    }
    return out


def _mention_users(db: Session, project: Project, ids: list[int] | None) -> list[User]:
    """Resolve mention ids to project members."""
    if not ids:
        return []
    return (
        db.query(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .filter(ProjectMember.project_id == project.id, User.id.in_(ids))
        .all()
    )


def _notify_mentions(db: Session, project: Project, actor: User, file_row: File | None,
                     comment: FileComment):
    """Create a personal notification for each mentioned member (the actor is
    excluded). Respects per-project mute preferences."""
    mentions = _mention_users(db, project, comment.mentions)
    prefs = dict(
        db.query(NotificationPref.user_id, NotificationPref.enabled)
        .filter(NotificationPref.project_id == project.id)
        .all()
    )
    link = f"/projects/{project.id}/explorer"
    if comment.drive_id:
        link += f"?drive={comment.drive_id}"
    for m in mentions:
        if m.id == actor.id:
            continue
        if prefs.get(m.id, True) is False:
            continue
        row = Notification(
            user_id=m.id,
            project_id=project.id,
            activity_id=None,
            type="mention",
            title="You were mentioned in a comment",
            description=f"{actor.name} mentioned you"
                        + (f" — {file_row.name}" if file_row else ""),
            link=link,
            is_read=False,
        )
        db.add(row)
    db.flush()


def _notify_user(db: Session, project: Project, user_id: int, notif_type: str,
                 title: str, description: str, link: str):
    """Direct notification to a single project member (mentions/approvals)."""
    pref = (
        db.query(NotificationPref.enabled)
        .filter(NotificationPref.project_id == project.id,
                NotificationPref.user_id == user_id)
        .first()
    )
    if pref is not None and pref.enabled is False:
        return
    db.add(Notification(
        user_id=user_id, project_id=project.id, activity_id=None,
        type=notif_type, title=title, description=description,
        link=link, is_read=False,
    ))
    db.flush()


def _extract_mentions(body: str, members: list[tuple[int, str, str]]) -> list[int]:
    """Parse '@name' / '@email' tokens out of body against project members."""
    ids: list[int] = []
    if not body or "@" not in body:
        return ids
    for uid, name, email in members:
        if name and f"@{name}" in body:
            ids.append(uid)
            continue
        if email and f"@{email}" in body:
            ids.append(uid)
    return ids


@router.get("/files/{drive_id}/comments", response_model=list[CommentOut])
def list_comments(project_id: int, drive_id: str,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    rows = (
        db.query(FileComment)
        .filter(FileComment.project_id == project.id,
                FileComment.drive_id == drive_id,
                FileComment.parent_id.is_(None))
        .order_by(FileComment.created_at.asc())
        .all()
    )
    return [_comment_out(db, c) for c in rows]


@router.get("/comments/{comment_id}", response_model=CommentOut)
def get_comment(project_id: int, comment_id: int,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    c = db.query(FileComment).filter(
        FileComment.id == comment_id, FileComment.project_id == project.id
    ).first()
    if c is None:
        raise NotFoundError("Comment not found.")
    return _comment_out(db, c)


@router.get("/comments/{comment_id}/replies", response_model=list[CommentOut])
def list_replies(project_id: int, comment_id: int,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    rows = (
        db.query(FileComment)
        .filter(FileComment.project_id == project.id, FileComment.parent_id == comment_id)
        .order_by(FileComment.created_at.asc())
        .all()
    )
    return [_comment_out(db, c) for c in rows]


@router.post("/files/{drive_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(project_id: int, drive_id: str, payload: CommentCreate, request: Request,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    row = _load_file(db, project, drive_id)
    if payload.parent_id:
        parent = db.query(FileComment).filter(
            FileComment.id == payload.parent_id, FileComment.project_id == project.id
        ).first()
        if parent is None:
            raise NotFoundError("Comment not found.")
        if parent.parent_id is not None:
            raise ConflictError("Replies are one level deep; reply to the top comment instead.")
    members = db.query(User.id, User.name, User.email).join(
        ProjectMember, ProjectMember.user_id == User.id
    ).filter(
        ProjectMember.project_id == project.id
    ).all()
    mentions = payload.mention_ids if payload.mention_ids else _extract_mentions(payload.body, members)
    comment = FileComment(project_id=project.id, drive_id=drive_id,
                          file_id=row.id, user_id=user.id, body=payload.body,
                          parent_id=payload.parent_id, mentions=mentions)
    db.add(comment)
    db.flush()
    _comment_activity(db, project, user, row, payload.body,
                      reply_to=payload.parent_id, mention_ids=mentions)
    if mentions:
        _notify_mentions(db, project, user, row, comment)
    record_audit(db, user=user, action="COMMENT", project_id=project_id,
                 resource_type="FILE", resource_id=drive_id,
                 details={"name": row.name, "reply_to": payload.parent_id,
                          "mentions": mentions},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    db.refresh(comment)
    return _comment_out(db, comment)


@router.get("/comments", response_model=list[CommentOut])
def list_project_comments(project_id: int,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Project-level general discussion comments (drive_id == '')."""
    project, _m, _role = get_project_for_user(project_id, user, db)
    rows = (
        db.query(FileComment)
        .filter(FileComment.project_id == project.id,
                FileComment.drive_id == "",
                FileComment.parent_id.is_(None))
        .order_by(FileComment.created_at.desc())
        .all()
    )
    return [_comment_out(db, c) for c in rows]


@router.post("/comments", response_model=CommentOut, status_code=201)
def add_project_comment(project_id: int, payload: CommentCreate, request: Request,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Post a general (project-level) discussion comment, optionally as a reply."""
    project, _m, _role = get_project_for_user(project_id, user, db)
    if payload.parent_id:
        parent = db.query(FileComment).filter(
            FileComment.id == payload.parent_id, FileComment.project_id == project.id
        ).first()
        if parent is None:
            raise NotFoundError("Comment not found.")
        if parent.drive_id != "":
            raise ConflictError("Cannot reply to a file comment with a general comment.")
        if parent.parent_id is not None:
            raise ConflictError("Replies are one level deep; reply to the top comment instead.")
    members = db.query(User.id, User.name, User.email).join(
        ProjectMember, ProjectMember.user_id == User.id
    ).filter(
        ProjectMember.project_id == project.id
    ).all()
    mentions = payload.mention_ids if payload.mention_ids else _extract_mentions(payload.body, members)
    comment = FileComment(project_id=project.id, drive_id="", file_id=None,
                          user_id=user.id, body=payload.body,
                          parent_id=payload.parent_id, mentions=mentions)
    db.add(comment)
    db.flush()
    _record_app_activity(
        db, project, user, ACTION_COMMENTED, "PROJECT", project.name,
        drive_id=None, file_id=None, path="", folder_path="",
        details={"comment": payload.body[:200]},
        exclude_user_id=user.id,
    )
    if mentions:
        _notify_mentions(db, project, user, None, comment)
    record_audit(db, user=user, action="PROJECT_COMMENT", project_id=project_id,
                 resource_type="PROJECT", resource_id=str(project_id),
                 details={"reply_to": payload.parent_id, "mentions": mentions},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    db.refresh(comment)
    return _comment_out(db, comment)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(project_id: int, comment_id: int, request: Request,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, role = get_project_for_user(project_id, user, db)
    comment = db.query(FileComment).filter(
        FileComment.id == comment_id, FileComment.project_id == project.id
    ).first()
    if comment is None:
        raise NotFoundError("Comment not found.")
    if comment.user_id != user.id and role != USER_ROLE_MANAGER and user.role != "ADMIN":
        raise ForbiddenError("You can only delete your own comments.")
    record_audit(db, user=user, action="DELETE_COMMENT", project_id=project_id,
                 resource_type="FILE", resource_id=comment.drive_id,
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    for a in list(comment.attachments):
        try:
            p = Path(a.stored_path)
            if p.exists() and p.is_relative_to(DATA_DIR):
                p.unlink(missing_ok=True)
        except Exception:
            pass
    db.delete(comment)
    db.commit()


# --- comment attachments ----------------------------------------------------


@router.post("/comments/{comment_id}/attachments", response_model=CommentAttachmentOut, status_code=201)
def add_attachment(request: Request, project_id: int, comment_id: int,
                   file: UploadFile = FastAPIFile(...),
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    comment = db.query(FileComment).filter(
        FileComment.id == comment_id, FileComment.project_id == project.id
    ).first()
    if comment is None:
        raise NotFoundError("Comment not found.")
    safe_name = Path(file.filename or "file").name[:200]
    folder = DATA_DIR / "attachments" / str(project.id)
    folder.mkdir(parents=True, exist_ok=True)
    content = file.file.read()
    if len(content) > settings.MAX_ATTACHMENT_BYTES:
        raise AppError("Attachment is too large.")
    stored = folder / f"c{comment_id}_{uuid4().hex[:10]}_{safe_name}"
    stored.write_bytes(content)
    att = FileCommentAttachment(
        project_id=project.id, comment_id=comment.id, user_id=user.id,
        filename=safe_name, stored_path=str(stored), size=len(content),
        content_type=file.content_type,
    )
    db.add(att)
    record_audit(db, user=user, action="COMMENT_ATTACH", project_id=project_id,
                 resource_type="COMMENT", resource_id=str(comment_id),
                 details={"filename": safe_name, "size": len(content)},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    db.refresh(att)
    return {
        "id": att.id,
        "filename": att.filename,
        "size": att.size,
        "content_type": att.content_type,
        "download_url": f"/api/projects/{project.id}/comments/{comment_id}/attachments/{att.id}/download",
        "created_at": att.created_at,
    }


@router.get("/comments/{comment_id}/attachments/{attachment_id}/download")
def download_attachment(project_id: int, comment_id: int, attachment_id: int,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    att = db.query(FileCommentAttachment).filter(
        FileCommentAttachment.id == attachment_id,
        FileCommentAttachment.comment_id == comment_id,
        FileCommentAttachment.project_id == project.id,
    ).first()
    if att is None:
        raise NotFoundError("Attachment not found.")
    try:
        p = Path(att.stored_path)
        if not p.exists():
            raise NotFoundError("Attachment file is missing.")
        return FileResponse(p, media_type=att.content_type or "application/octet-stream",
                            filename=att.filename)
    except (OSError, ValueError):
        raise NotFoundError("Attachment file is missing.") from None


# --- approvals --------------------------------------------------------------

APPROVAL_PENDING = "PENDING"
APPROVAL_APPROVED = "APPROVED"
APPROVAL_REJECTED = "REJECTED"


def _approval_out(a: FileApproval) -> dict:
    rb = a.requested_by
    rv = a.reviewer
    dec = a.decided_by
    return {
        "id": a.id,
        "drive_id": a.drive_id,
        "file_id": a.file_id,
        "file_name": a.file.name if a.file else None,
        "status": a.status,
        "comment": a.comment,
        "requested_by_user_id": a.requested_by_user_id,
        "requested_by_name": rb.name if rb else "Unknown",
        "requested_by_email": rb.email if rb else "",
        "reviewer_user_id": a.reviewer_user_id,
        "reviewer_name": rv.name if rv else None,
        "decided_by_user_id": a.decided_by_user_id,
        "decided_comment": a.decided_comment,
        "created_at": a.created_at,
        "decided_at": a.decided_at,
    }


@router.get("/files/{drive_id}/approvals", response_model=list[FileApprovalOut])
def list_approvals(project_id: int, drive_id: str,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    rows = (
        db.query(FileApproval)
        .filter(FileApproval.project_id == project.id, FileApproval.drive_id == drive_id)
        .order_by(FileApproval.created_at.desc())
        .all()
    )
    return [_approval_out(a) for a in rows]


@router.post("/files/{drive_id}/approvals", response_model=FileApprovalOut, status_code=201)
def request_approval(project_id: int, drive_id: str, payload: FileApprovalCreate, request: Request,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    _can_edit(_role)
    row = _load_file(db, project, drive_id)
    if payload.reviewer_user_id == user.id:
        raise ConflictError("You cannot request approval from yourself.")
    reviewer = db.query(ProjectMember).filter(
        ProjectMember.project_id == project.id,
        ProjectMember.user_id == payload.reviewer_user_id,
    ).first()
    if reviewer is None:
        raise NotFoundError("Reviewer is not a project member.")
    approval = FileApproval(
        project_id=project.id, drive_id=drive_id, file_id=row.id,
        requested_by_user_id=user.id, reviewer_user_id=payload.reviewer_user_id,
        comment=payload.comment,
    )
    db.add(approval)
    db.flush()
    _record_app_activity(
        db, project, user, "APPROVAL_REQUESTED", "FILE", row.name,
        drive_id=drive_id, file_id=row.id, path=row.path,
        folder_path=row.path.rsplit("/", 1)[0] if "/" in row.path else "",
        details={"reviewer": reviewer.user.email if reviewer.user else "",
                 "comment": (payload.comment or "")[:200]},
        exclude_user_id=payload.reviewer_user_id,
    )
    _notify_user(
        db, project, payload.reviewer_user_id,
        "approval_request", "Approval requested",
        f"{user.name} requests approval on {row.name}",
        link=f"/projects/{project.id}/explorer?drive={drive_id}",
    )
    record_audit(db, user=user, action="REQUEST_APPROVAL", project_id=project_id,
                 resource_type="FILE", resource_id=drive_id,
                 details={"name": row.name, "reviewer_id": payload.reviewer_user_id},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    db.commit()
    db.refresh(approval)
    return _approval_out(approval)


@router.post("/approvals/{approval_id}/decide", response_model=FileApprovalOut)
def decide_approval(approval_id: int, payload: FileApprovalDecision, request: Request,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    approval = db.query(FileApproval).filter(FileApproval.id == approval_id).first()
    if approval is None:
        raise NotFoundError("Approval request not found.")
    if approval.status != APPROVAL_PENDING:
        raise ConflictError("This request has already been decided.")
    if payload.status not in (APPROVAL_APPROVED, APPROVAL_REJECTED):
        raise ConflictError("Status must be APPROVED or REJECTED.")
    project = approval.project
    membership = db.query(ProjectMember).filter(
        ProjectMember.project_id == approval.project_id,
        ProjectMember.user_id == user.id,
    ).first()
    is_reviewer = approval.reviewer_user_id == user.id
    is_manager = user.role == "ADMIN" or (membership and membership.role == USER_ROLE_MANAGER)
    if not (is_reviewer or is_manager):
        raise ForbiddenError("Only the assigned reviewer (or a manager) can decide this request.")
    approval.status = payload.status
    approval.decided_by_user_id = user.id
    approval.decided_comment = payload.comment
    approval.decided_at = utcnow()
    record_audit(db, user=user, action=f"APPROVE_{payload.status}", project_id=approval.project_id,
                 resource_type="FILE", resource_id=approval.drive_id,
                 details={"status": payload.status, "comment": payload.comment},
                 ip=_ip(request), user_agent=request.headers.get("user-agent"))
    spec = ("approved", "Approval granted") if payload.status == APPROVAL_APPROVED else ("rejected", "Approval rejected")
    target_name = approval.file.name if approval.file else approval.drive_id
    _notify_user(
        db, approval.project, approval.requested_by_user_id,
        spec[0], spec[1],
        f"{user.name} {spec[0].lower()} approval on {target_name}",
        link=f"/projects/{approval.project_id}/explorer?drive={approval.drive_id}",
    )
    _record_app_activity(
        db, approval.project, user, "APPROVED" if payload.status == APPROVAL_APPROVED else "REJECTED",
        "FILE", target_name,
        drive_id=approval.drive_id, file_id=approval.file_id, path="", folder_path="",
        details={"comment": payload.comment, "decider": user.email},
        exclude_user_id=user.id,
    )
    db.commit()
    db.refresh(approval)
    return _approval_out(approval)


@router.get("/approvals", response_model=list[FileApprovalOut])
def list_project_approvals(project_id: int, status: str = "all",
                           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _role = get_project_for_user(project_id, user, db)
    q = db.query(FileApproval).filter(FileApproval.project_id == project.id)
    if status == "pending":
        q = q.filter(FileApproval.status == APPROVAL_PENDING)
    elif status == "approved":
        q = q.filter(FileApproval.status == APPROVAL_APPROVED)
    elif status == "rejected":
        q = q.filter(FileApproval.status == APPROVAL_REJECTED)
    rows = q.order_by(FileApproval.created_at.desc()).limit(200).all()
    return [_approval_out(a) for a in rows]