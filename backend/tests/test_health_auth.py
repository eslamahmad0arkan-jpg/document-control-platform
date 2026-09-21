"""Health, auth, session security tests."""
from __future__ import annotations

from datetime import datetime, timedelta


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_me_requires_auth(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_me_with_valid_session(client, db, helper):
    user = helper.make_user(db, "me@example.com")
    helper.login_as(client, user)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "me@example.com"
    assert body["unread_notifications"] == 0


def test_me_with_tampered_cookie(client, db, helper):
    user = helper.make_user(db, "tamper@example.com")
    from app.config import settings

    client.cookies.set(settings.TOKEN_COOKIE_NAME, "garbage.token.value")
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_login_returns_google_url(client):
    resp = client.get("/api/auth/login")
    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=" in url
    assert "access_type=offline" in url


def test_callback_state_mismatch(client):
    resp = client.get("/api/auth/callback?code=x&state=wrong")
    assert resp.status_code == 401


def test_logout_clears_session(client, db, helper):
    user = helper.make_user(db, "logout@example.com")
    helper.login_as(client, user)
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 204
    client.cookies.clear()
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_get_valid_access_token_handles_naive_expiry(db, helper, monkeypatch):
    """SQLite returns naive datetimes; comparisons must not raise TypeError."""
    import pytest

    from app.errors import GoogleAuthExpiredError
    from app.models import GoogleAccount, GoogleToken
    from app.security import encrypt_secret
    from app.services.google.oauth import get_valid_access_token

    user = helper.make_user(db, "tz@example.com")
    account = GoogleAccount(user_id=user.id, google_sub="sub-tz@example.com",
                            email="tz@example.com", name="T")
    db.add(account)
    db.commit()
    token = GoogleToken(
        user_id=user.id, account_id=account.id,
        access_token=encrypt_secret("tok-a"), refresh_token=encrypt_secret("ref-a"),
        expires_at=datetime.now().replace(tzinfo=None) + timedelta(days=1),
    )
    db.add(token)
    db.commit()

    assert get_valid_access_token(db, user) == "tok-a"

    token.expires_at = datetime.now().replace(tzinfo=None) - timedelta(days=1)
    db.commit()

    def _boom(db, user, token=None):
        raise GoogleAuthExpiredError(details={"needReconnect": True})

    monkeypatch.setattr("app.services.google.oauth.refresh_access_token", _boom)
    with pytest.raises(GoogleAuthExpiredError):
        get_valid_access_token(db, user)