"""Google OAuth 2.0 flow. Users authenticate with their own Google accounts;
no Google passwords are ever requested. Tokens are stored encrypted at rest."""
from __future__ import annotations

import json
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ...config import settings
from ...errors import AppError, AuthError, GoogleAuthExpiredError, GoogleConnectionError
from ...models import GoogleAccount, GoogleToken, User, utcnow
from ...security import decrypt_secret, encrypt_secret

OAUTH_SCOPES = settings.GOOGLE_SCOPES


def build_authorization_url(state: str, redirect_uri: str | None = None) -> str:
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri or settings.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{settings.OAUTH_AUTH_URI}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str, redirect_uri: str | None = None) -> dict:
    """Swap the authorization code for tokens."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise AppError("Google OAuth is not configured. See GOOGLE_OAUTH_SETUP.md.",
                       details={"misconfigured": True})
    try:
        resp = httpx.post(
            settings.OAUTH_TOKEN_URI,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri or settings.OAUTH_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise GoogleConnectionError("Could not reach Google OAuth endpoint.") from exc
    if resp.status_code != 200:
        detail = resp.text[:500]
        raise AuthError(f"Google rejected the authorization code: {detail}")
    return resp.json()


def fetch_userinfo(access_token: str) -> dict:
    try:
        resp = httpx.get(
            settings.OAUTH_USERINFO_URI,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise GoogleConnectionError("Could not reach Google userinfo endpoint.") from exc
    if resp.status_code != 200:
        raise AuthError("Could not read Google profile.")
    return resp.json()


def refresh_access_token(db: Session, user: User, token: GoogleToken | None = None) -> str:
    """Refresh the user's Google access token using the stored refresh token.
    Returns the decrypted, valid access token. Raises if refresh is impossible."""
    if token is None:
        token = _get_token(db, user)
    if token is None or not token.refresh_token:
        raise GoogleAuthExpiredError(details={"needReconnect": True})
    refresh = decrypt_secret(token.refresh_token)
    try:
        resp = httpx.post(
            settings.OAUTH_TOKEN_URI,
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise GoogleConnectionError("Could not reach Google when refreshing token.") from exc
    if resp.status_code != 200:
        raise GoogleAuthExpiredError(
            details={"needReconnect": True, "reason": "refresh_token_revoked"}
        )
    data = resp.json()
    new_access = data.get("access_token", "")
    expires_in = data.get("expires_in", 3600)
    token.access_token = encrypt_secret(new_access)
    token.expires_at = utcnow() + timedelta(seconds=int(expires_in))
    token.updated_at = utcnow()
    db.add(token)
    db.commit()
    return new_access


def _get_token(db: Session, user: User) -> GoogleToken | None:
    return (
        db.query(GoogleToken)
        .filter(GoogleToken.user_id == user.id)
        .order_by(GoogleToken.id.desc())
        .first()
    )


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime to timezone-aware UTC. SQLite stores naive datetimes,
    so values read from the DB must be treated as UTC before any comparison."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_valid_access_token(db: Session, user: User) -> str:
    """Return a usable (fresh) Google access token for the user, refreshing if needed."""
    token = _get_token(db, user)
    if token is None:
        raise GoogleAuthExpiredError(details={"needReconnect": True})

    def _now() -> datetime:
        return datetime.now(timezone.utc)

    expires_at = _as_utc(token.expires_at)
    if not expires_at or expires_at < _now() + timedelta(seconds=60):
        return refresh_access_token(db, user, token)
    try:
        return decrypt_secret(token.access_token)
    except ValueError:
        raise AuthError("Stored Google token cannot be decrypted.")


def complete_login(db: Session, code: str, redirect_uri: str | None = None) -> User:
    """End-to-end: code -> tokens -> userinfo -> upsert user + accounts/tokens."""
    token_data = exchange_code(code, redirect_uri=redirect_uri)
    access_token = token_data.get("access_token")
    if not access_token:
        raise AuthError("Google did not return an access token.")
    info = fetch_userinfo(access_token)

    sub = info.get("sub")
    email = (info.get("email") or "").lower()
    if not sub or not email:
        raise AuthError("Google profile is missing identity fields.")

    # The authorization code can only be exchanged once, so the network work is
    # done above and only the DB persistence is retried below. Under heavy
    # monitoring the SQLite writer can briefly be busy ("database is locked").
    for attempt in range(5):
        try:
            return _persist_login(db, token_data, info, sub, email, access_token)
        except OperationalError as exc:
            if "database is locked" not in str(exc):
                raise
            time.sleep(0.4 * (attempt + 1))
            db.rollback()
    raise OperationalError("login write failed: database stayed locked", None, None)


def _persist_login(
    db: Session, token_data: dict, info: dict, sub: str, email: str, access_token: str
) -> User:
    user = db.query(User).filter(User.google_sub == sub).first()
    if user is None:
        user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            email=email,
            name=info.get("name") or email.split("@")[0],
            picture=info.get("picture"),
            google_sub=sub,
            role="VIEWER",
            is_active=True,
        )
        db.add(user)
        db.flush()
    else:
        user.google_sub = sub
        user.name = info.get("name") or user.name
        user.picture = info.get("picture") or user.picture
        user.is_active = True
        user.last_login_at = utcnow()

    account = db.query(GoogleAccount).filter(GoogleAccount.google_sub == sub).first()
    if account is None:
        account = GoogleAccount(
            user_id=user.id,
            google_sub=sub,
            email=email,
            name=info.get("name"),
            picture=info.get("picture"),
        )
        db.add(account)
        db.flush()

    token_row = _get_token(db, user)
    expires_in = int(token_data.get("expires_in", 3600))
    if token_row is None:
        token_row = GoogleToken(user_id=user.id, account_id=account.id)
        db.add(token_row)
    token_row.account_id = account.id
    token_row.access_token = encrypt_secret(access_token)
    if token_data.get("refresh_token"):
        token_row.refresh_token = encrypt_secret(token_data["refresh_token"])
    if token_data.get("id_token"):
        token_row.id_token = encrypt_secret(token_data["id_token"])
    token_row.scope = token_data.get("scope") or OAUTH_SCOPES
    token_row.expires_at = utcnow() + timedelta(seconds=expires_in)
    token_row.updated_at = utcnow()

    user.last_login_at = utcnow()
    db.commit()
    db.refresh(user)
    return user


def new_state_token() -> str:
    return secrets.token_urlsafe(32)


def remaining_lifetime(token: GoogleToken) -> int:
    expires_at = _as_utc(token.expires_at)
    if not expires_at:
        return 0
    return int((expires_at - datetime.now(timezone.utc)).total_seconds())


def token_info_json(db: Session, user: User) -> dict:
    token = _get_token(db, user)
    if token is None:
        return {"connected": False}
    return {
        "connected": True,
        "has_refresh_token": bool(token.refresh_token),
        "expires_in": remaining_lifetime(token),
        "scope": token.scope,
    }