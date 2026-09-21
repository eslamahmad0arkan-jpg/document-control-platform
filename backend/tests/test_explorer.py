"""Explorer listing, search, and Drive mutation endpoints (viewer vs editor)."""
from __future__ import annotations


def _patch_explorer_drive(monkeypatch, fake):
    import app.routers.explorer as ex

    monkeypatch.setattr(ex, "build_drive_client", lambda db, user: fake)


def test_explorer_listing(client, db, helper):
    owner = helper.make_user(db, "exp@example.com")
    project = helper.make_project(db, owner)
    helper.seed_snapshot(db, project)
    helper.login_as(client, owner)

    resp = client.get(f"/api/projects/{project.id}/explorer?folder_id=root-abc")
    assert resp.status_code == 200
    body = resp.json()
    names = [f["name"] for f in body["folders"]]
    assert "Drawings" in names and "Technical" in names
    assert "Civil" not in names  # nested, not at root

    resp = client.get(f"/api/projects/{project.id}/explorer?folder_id=f-technical")
    body = resp.json()
    assert [f["name"] for f in body["folders"]] == ["Civil"]
    assert [f["name"] for f in body["files"]] == ["BOQ.xlsx"]
    assert [b["name"] for b in body["breadcrumbs"]] == ["ATIKA", "Technical"]


def test_explorer_search(client, db, helper):
    owner = helper.make_user(db, "srch@example.com")
    project = helper.make_project(db, owner, name="WS")
    helper.seed_snapshot(db, project)
    helper.login_as(client, owner)
    resp = client.get(f"/api/projects/{project.id}/search?q=rfi")
    assert resp.status_code == 200
    assert any(i["name"] == "RFI-025.pdf" for i in resp.json()["items"])
    assert all(i["type"] in ("FILE", "FOLDER", "ACTIVITY") for i in resp.json()["items"])


def test_viewer_cannot_mutate(client, db, helper, monkeypatch):
    owner = helper.make_user(db, "vowner@example.com")
    viewer = helper.make_user(db, "vviewer@example.com")
    project = helper.make_project(db, owner)
    __import__("app.models", fromlist=["ProjectMember"]).ProjectMember
    from app.models import ProjectMember

    db.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="VIEWER",
                         added_by=owner.id))
    db.commit()
    helper.login_as(client, viewer)

    resp = client.post(f"/api/projects/{project.id}/folders",
                       json={"parent_id": "root-abc", "name": "X"})
    assert resp.status_code in (403, 400)
    if resp.status_code == 403:
        assert "read-only" in resp.json()["error"]["message"]


def test_editor_can_create_folder(client, db, helper, monkeypatch):
    from tests.fakes import FakeDrive

    owner = helper.make_user(db, "eowner@example.com")
    editor = helper.make_user(db, "eedit@example.com")
    project = helper.make_project(db, owner)
    from app.models import ProjectMember

    db.add(ProjectMember(project_id=project.id, user_id=editor.id, role="EDITOR",
                         added_by=owner.id))
    db.commit()
    fake = FakeDrive()
    _patch_explorer_drive(monkeypatch, fake)
    helper.login_as(client, editor)

    resp = client.post(f"/api/projects/{project.id}/folders",
                       json={"parent_id": "root-abc", "name": "Contracts"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "Contracts"
    assert resp.json()["type"] == "FOLDER"

    from app.models import Activity

    acts = db.query(Activity).filter(Activity.project_id == project.id).all()
    assert any(a.action == "CREATE_FOLDER" for a in acts)


def test_upload_rename_move_trash(client, db, helper, monkeypatch):
    from app.models import Activity
    from tests.fakes import FakeDrive

    owner = helper.make_user(db, "urmt@example.com")
    project = helper.make_project(db, owner)
    helper.seed_snapshot(db, project)
    fake = FakeDrive()
    _patch_explorer_drive(monkeypatch, fake)
    helper.login_as(client, owner)

    # upload
    resp = client.post(f"/api/projects/{project.id}/upload?folder_id=f-corr",
                       files={"file": ("NEW-SLIP.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "NEW-SLIP.pdf"

    # rename
    resp = client.post(f"/api/projects/{project.id}/rename",
                       json={"drive_id": "f-boq", "name": "BOQ-FINAL.xlsx"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "BOQ-FINAL.xlsx"

    # move
    resp = client.post(f"/api/projects/{project.id}/move",
                       json={"drive_id": "f-dwg001", "parent_id": "f-reports"})
    assert resp.status_code == 200
    node = resp.json()
    assert node["type"] == "FILE"

    # trash
    resp = client.post(f"/api/projects/{project.id}/trash",
                       json={"drive_id": "f-rfi"})
    assert resp.status_code == 200

    acts = db.query(Activity).filter(Activity.project_id == project.id).all()
    actions = {a.action for a in acts}
    assert {"UPLOAD_FILE", "RENAMED", "MOVED", "TRASHED"} <= actions


def test_mutation_on_missing_item_404(client, db, helper, monkeypatch):
    from tests.fakes import FakeDrive

    owner = helper.make_user(db, "miss@example.com")
    project = helper.make_project(db, owner)
    fake = FakeDrive()
    _patch_explorer_drive(monkeypatch, fake)
    helper.login_as(client, owner)
    resp = client.post(f"/api/projects/{project.id}/rename",
                       json={"drive_id": "does-not-exist", "name": "X"})
    assert resp.status_code == 404


def _patch_pick_identity(monkeypatch, fake):
    import app.routers.explorer as ex

    monkeypatch.setattr(ex, "pick_sync_identity", lambda db, project, user: (user, fake))


def test_file_content_streams_bytes(client, db, helper, monkeypatch):
    from tests.fakes import FakeDrive

    owner = helper.make_user(db, "content@example.com")
    project = helper.make_project(db, owner)
    helper.seed_snapshot(db, project)
    fake = FakeDrive()
    _patch_pick_identity(monkeypatch, fake)
    helper.login_as(client, owner)

    resp = client.get(f"/api/projects/{project.id}/files/f-dwg001/content")
    assert resp.status_code == 200
    assert resp.content == b"content-of:dwg-001.dwg"
    assert "inline" in resp.headers["content-disposition"]


def test_file_content_google_native_exported_to_pdf(client, db, helper, monkeypatch):
    from tests.fakes import FakeDrive

    owner = helper.make_user(db, "native@example.com")
    project = helper.make_project(db, owner)
    helper.seed_snapshot(db, project)
    from app.models import File

    row = db.query(File).filter(File.drive_id == "f-boq").first()
    row.mime_type = "application/vnd.google-apps.spreadsheet"
    db.commit()
    fake = FakeDrive()
    _patch_pick_identity(monkeypatch, fake)
    helper.login_as(client, owner)

    resp = client.get(f"/api/projects/{project.id}/files/f-boq/content")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 fake-export"
    assert resp.headers["content-type"].startswith("application/pdf")


def test_file_content_download_flag_and_404(client, db, helper, monkeypatch):
    from tests.fakes import FakeDrive

    owner = helper.make_user(db, "dl@example.com")
    project = helper.make_project(db, owner)
    helper.seed_snapshot(db, project)
    fake = FakeDrive()
    _patch_pick_identity(monkeypatch, fake)
    helper.login_as(client, owner)

    resp = client.get(f"/api/projects/{project.id}/files/f-dwg001/content?download=true")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]

    resp = client.get(f"/api/projects/{project.id}/files/does-not-exist/content")
    assert resp.status_code == 404