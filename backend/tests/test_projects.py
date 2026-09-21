"""Project CRUD, members, stats."""
from __future__ import annotations


def test_list_projects_empty(client, db, helper):
    user = helper.make_user(db, "manager@example.com")
    helper.login_as(client, user)
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_project_with_drive(client, db, helper, monkeypatch):
    from tests.fakes import FakeDrive

    user = helper.make_user(db, "creator@example.com")
    helper.login_as(client, user)

    fake = FakeDrive()
    monkeypatch.setattr("app.routers.projects.pick_sync_identity",
                        lambda db, project, user: (user, fake))
    resp = client.post("/api/projects", json={
        "name": "ATIKA", "google_folder_id": "root-abc",
        "description": "Tower project",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["google_folder_id"] == "root-abc"
    assert body["role"] == "PROJECT_MANAGER"
    assert len(body["members"]) == 1
    # creator becomes a member
    assert body["members"][0]["email"] == "creator@example.com"


def test_create_project_rejects_duplicate_folder(client, db, helper, monkeypatch):
    from tests.fakes import FakeDrive

    user = helper.make_user(db, "dup@example.com")
    helper.login_as(client, user)
    monkeypatch.setattr("app.routers.projects.pick_sync_identity",
                        lambda db, project, user: (user, FakeDrive()))
    first = client.post("/api/projects", json={"name": "A", "google_folder_id": "root-abc"})
    assert first.status_code == 201
    second = client.post("/api/projects", json={"name": "B", "google_folder_id": "root-abc"})
    assert second.status_code == 409


def test_create_project_rejects_non_folder(client, db, helper, monkeypatch):
    from tests.fakes import FakeDrive

    user = helper.make_user(db, "nof@example.com")
    helper.login_as(client, user)
    fake = FakeDrive()
    monkeypatch.setattr("app.routers.projects.pick_sync_identity",
                        lambda db, project, user: (user, fake))
    resp = client.post("/api/projects", json={"name": "X", "google_folder_id": "f-boq"})
    assert resp.status_code == 400
    assert "not a folder" in resp.json()["error"]["message"]


def test_project_detail_and_stats(client, db, helper):
    owner = helper.make_user(db, "owner@example.com")
    viewer = helper.make_user(db, "viewer@example.com")
    project = helper.make_project(db, owner)
    db.add(__import__("app.models", fromlist=["ProjectMember"]).ProjectMember(
        project_id=project.id, user_id=viewer.id, role="VIEWER", added_by=owner.id))
    db.commit()

    helper.login_as(client, viewer)
    resp = client.get(f"/api/projects/{project.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "VIEWER"
    assert body["stats"]["total_files"] == 0
    assert len(body["members"]) == 2


def test_non_member_cannot_access_project(client, db, helper):
    owner = helper.make_user(db, "owner2@example.com")
    intruder = helper.make_user(db, "intruder@example.com")
    project = helper.make_project(db, owner)
    helper.login_as(client, intruder)
    resp = client.get(f"/api/projects/{project.id}")
    assert resp.status_code == 403


def test_update_project_permissions(client, db, helper):
    owner = helper.make_user(db, "pm@example.com")
    editor = helper.make_user(db, "editor@example.com")
    project = helper.make_project(db, owner)
    db.add(__import__("app.models", fromlist=["ProjectMember"]).ProjectMember(
        project_id=project.id, user_id=editor.id, role="EDITOR", added_by=owner.id))
    db.commit()

    helper.login_as(client, editor)
    resp = client.patch(f"/api/projects/{project.id}", json={"description": "hacked"})
    assert resp.status_code == 403

    helper.login_as(client, owner)
    resp = client.patch(f"/api/projects/{project.id}", json={"description": "ok"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "ok"


def test_delete_project_admin_only(client, db, helper):
    owner = helper.make_user(db, "deldel@example.com")
    project = helper.make_project(db, owner)
    other = helper.make_user(db, "other@example.com", role="ADMIN")
    db.add(__import__("app.models", fromlist=["ProjectMember"]).ProjectMember(
        project_id=project.id, user_id=other.id, role="VIEWER", added_by=owner.id))
    db.commit()

    helper.login_as(client, other)
    resp = client.delete(f"/api/projects/{project.id}")
    assert resp.status_code == 204
    resp = client.get("/api/projects")
    assert resp.json() == []


def test_trigger_sync_endpoint(client, db, helper):
    owner = helper.make_user(db, "syncowner@example.com")
    project = helper.make_project(db, owner)
    helper.login_as(client, owner)
    resp = client.post(f"/api/projects/{project.id}/sync")
    assert resp.status_code == 200
    assert resp.json()["phase"] in ("idle", "error", "scanning")
    # worker disabled in tests — poll for a moment and expect an error (no creds) or idle
    assert "last_error" in resp.json()