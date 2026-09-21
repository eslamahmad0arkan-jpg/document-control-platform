"""FastAPI dependencies: DB session, current user, project authorization."""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .errors import AuthError, ForbiddenError, NotFoundError
from .models import Project, ProjectMember, User, USER_ROLE_ADMIN, USER_ROLE_MANAGER
from .security import decode_session_token


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(settings.TOKEN_COOKIE_NAME)
    if not token:
        # allow Authorization: Bearer for API clients
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        raise AuthError("Authentication required.")
    payload = decode_session_token(token)
    if not payload:
        raise AuthError("Session is invalid or expired.")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise AuthError("Account not found or disabled.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != USER_ROLE_ADMIN:
        raise ForbiddenError("Administrator privileges are required.")
    return user


def get_project_for_user(project_id: int, user: User, db: Session,
                         required_role: str | None = None) -> tuple[Project, ProjectMember, str]:
    """Resolve a project and the caller's membership. Prevents IDOR.

    Returns (project, membership, effective_role).
    """
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise NotFoundError("Project not found.")
    member = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        .first()
    )
    if user.role == USER_ROLE_ADMIN:
        # admins can manage any project; synthesize a MANAGER membership for them
        eff = USER_ROLE_MANAGER
        return project, member, eff
    if not member:
        raise ForbiddenError("You are not a member of this project.")
    eff = member.role
    if required_role and not _role_at_least(eff, required_role):
        raise ForbiddenError("Your role does not allow this operation.")
    return project, member, eff


def _role_at_least(role: str, minimum: str) -> bool:
    rank = {USER_ROLE_ADMIN: 4, USER_ROLE_MANAGER: 3, "EDITOR": 2, "VIEWER": 1}
    return rank.get(role, 0) >= rank.get(minimum, 0)