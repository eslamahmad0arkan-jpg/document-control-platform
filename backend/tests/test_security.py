"""Security: roles, IDOR, admin-only endpoints."""
from __future__ import annotations

from app.models import ProjectMember


def test_viewer_cannot_read_other_projects_activities(client, db, helper):
    owner = helper.make_user(db, "fo@example.com")
    intruder = helper.make_user(db, "foi@example.com")
    project = helper.make_project(db, owner)
    helper.login_as(client, intruder)
    for path in (
        f"/api/projects/{project.id}/activities",
        f"/api/projects/{project.id}/explorer",
        f"/api/projects/{project.id}/reports",
        f"/api/projects/{project.id}/search?q=x",
    ):
        resp = client.get(path)
        assert resp.status_code == 403, path


def test_admin_required(client, db, helper):
    normal = helper.make_user(db, "normal@example.com")
    helper.login_as(client, normal)
    for path in ("/api/admin/users", "/api/admin/audit", "/api/admin/worker"):
        assert client.get(path).status_code == 403, path


def test_admin_user_management(client, db, helper):
    admin = helper.make_user(db, "root@example.com", role="ADMIN")
    target = helper.make_user(db, "staff@example.com", role="VIEWER")
    helper.login_as(client, admin)

    resp = client.patch(f"/api/admin/users/{target.id}",
                        json={"role": "PROJECT_MANAGER"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "PROJECT_MANAGER"

    resp = client.get("/api/admin/users")
    assert resp.status_code == 200
    assert any(u["email"] == "staff@example.com" for u in resp.json())


def test_admin_cannot_demote_self(client, db, helper):
    admin = helper.make_user(db, "root2@example.com", role="ADMIN")
    helper.login_as(client, admin)
    resp = client.patch(f"/api/admin/users/{admin.id}", json={"role": "VIEWER"})
    assert resp.status_code == 403


def test_audit_log_written_on_login_and_actions(client, db, helper):
    from app.models import AuditLog

    user = helper.make_user(db, "auditlog@example.com")
    project = helper.make_project(db, user)
    helper.login_as(client, user)
    client.post(f"/api/projects/{project.id}/sync")
    assert db.query(AuditLog).filter(AuditLog.action == "SYNC_TRIGGERED").count() >= 1

    admin = helper.make_user(db, "auditadmin@example.com", role="ADMIN")
    helper.login_as(client, admin)
    resp = client.get("/api/admin/audit")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
    actions = [a["action"] for a in resp.json()["items"]]
    assert "SYNC_TRIGGERED" in actions


def test_token_encryption_roundtrip(db):
    from app.security import decrypt_secret, encrypt_secret

    secret = "super-secret-refresh-token"
    cipher = encrypt_secret(secret)
    assert cipher != secret
    assert decrypt_secret(cipher) == secret