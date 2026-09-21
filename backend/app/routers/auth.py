"""Google OAuth login flow."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..errors import AuthError
from ..models import User
from ..schemas import LoginUrlResponse, MeResponse, UserPublic
from ..security import create_session_token
from ..services.audit_service import record_audit
from ..services.google import oauth
from ..services.notification_service import unread_count

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/login", response_model=LoginUrlResponse)
def login(request: Request):
    state = oauth.new_state_token()
    response = JSONResponse(
        content={"url": oauth.build_authorization_url(state)}
    )
    response.set_cookie(
        "oauth_state",
        state,
        max_age=600,
        httponly=True,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )
    return response


@router.get("/callback")
def callback(code: str, state: str, request: Request, db: Session = Depends(get_db)):
    cookie_state = request.cookies.get("oauth_state")
    if not cookie_state or cookie_state != state:
        raise AuthError("OAuth state mismatch — please try again.")
    user = oauth.complete_login(db, code)
    token = create_session_token(user.id, user.google_sub, user.role)
    response = Response(status_code=302)
    response.headers["Location"] = "/"
    response.set_cookie(
        settings.TOKEN_COOKIE_NAME, token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True, samesite=settings.SESSION_COOKIE_SAMESITE,
        secure=settings.SESSION_COOKIE_SECURE,
    )
    record_audit(db, user=user, action="LOGIN")
    db.commit()
    return response


@router.post("/logout")
def logout(request: Request, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    record_audit(db, user=user, action="LOGOUT")
    db.commit()
    response = Response(status_code=204)
    response.set_cookie(
        settings.TOKEN_COOKIE_NAME, "", expires=0, max_age=0,
        path="/", httponly=True,
        samesite=settings.SESSION_COOKIE_SAMESITE, secure=settings.SESSION_COOKIE_SECURE,
    )
    response.delete_cookie(
        settings.TOKEN_COOKIE_NAME, path="/",
        samesite=settings.SESSION_COOKIE_SAMESITE, secure=settings.SESSION_COOKIE_SECURE,
    )
    return response


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return MeResponse(
        user=UserPublic.model_validate(user),
        unread_notifications=unread_count(db, user.id),
    )


@router.get("/connection")
def connection_info(user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    info = oauth.token_info_json(db, user)
    info["scopes"] = settings.GOOGLE_SCOPES.split()
    return info