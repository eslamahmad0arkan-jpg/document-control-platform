"""Sync engine: full scan, incremental change diffing, activity detection."""
from __future__ import annotations

from app.models import File, Folder, SyncState, Activity, Notification
from app.services.sync import full_scan, incremental_sync


def _patch_drive(monkeypatch, fake):
    import app.services.sync as sync_mod

    monkeypatch.setattr(sync_mod, "build_drive_client", lambda db, user: fake)


def _attach_token(db, user):
    from app.models import GoogleAccount, GoogleToken
    from app.security import encrypt_secret

    acc = db.query(GoogleAccount).filter(GoogleAccount.user_id == user.id).first()
    if acc is None:
        acc = GoogleAccount(user_id=user.id, google_sub=f"sub-{user.email}",
                            email=user.email)
        db.add(acc)
        db.flush()
    db.add(GoogleToken(user_id=user.id, account_id=acc.id,
                       access_token=encrypt_secret("fake-access"),
                       refresh_token=encrypt_secret("fake-refresh"),
                       expires_at=None))
    db.commit()


def test_full_scan_populates_snapshot(db, helper, monkeypatch):
    user = helper.make_user(db, "scan@example.com")
    project = helper.make_project(db, user)
    from tests.fakes import FakeDrive

    fake = FakeDrive()
    _patch_drive(monkeypatch, fake)

    result = full_scan(db, project, user)
    assert result["folders"] == 5
    assert result["files"] == 3

    folders = db.query(Folder).filter(Folder.project_id == project.id).all()
    by_id = {f.drive_id: f for f in folders}
    assert by_id["f-technical"].path == "Technical"
    assert by_id["f-civil"].path == "Technical/Civil"

    files = db.query(File).filter(File.project_id == project.id).all()
    fmap = {f.drive_id: f for f in files}
    assert fmap["f-dwg001"].path == "Drawings/dwg-001.dwg"
    assert fmap["f-rfi"].path == "Correspondence/RFI-025.pdf"

    state = db.query(SyncState).filter(SyncState.project_id == project.id).first()
    assert state.last_full_scan_at is not None
    assert state.scanning_now is False


def test_full_scan_resolves_shortcuts(db, helper, monkeypatch):
    """Shortcuts to folders become folders (children pulled in); shortcuts to
    files become their target files; no placeholder shortcut rows remain."""
    user = helper.make_user(db, "shortcut@example.com")
    project = helper.make_project(db, user)
    from tests.fakes import FakeDrive, FOLDER, SHORTCUT, _node

    storage = {}
    storage["root-abc"] = _node("root-abc", "PROJECT", None, mime=FOLDER)
    storage["f-eleven"] = _node("f-eleven", "ELEVEN", "root-abc", mime=FOLDER)
    # 03-Drawing is a shortcut to the real folder tgt-draw
    storage["f-sc-draw"] = _node("f-sc-draw", "03-Drawing", "f-eleven",
                                 shortcut_target=("tgt-draw", FOLDER))
    storage["tgt-draw"] = _node("tgt-draw", "03-Drawing", "discarded-1", mime=FOLDER)
    storage["f-drw1"] = _node("f-drw1", "A-101.dwg", "tgt-draw", size=102400)
    # RFP is a shortcut to a real pdf tgt-rfp
    storage["f-sc-rfp"] = _node("f-sc-rfp", "RFP.pdf", "f-eleven",
                                shortcut_target=("tgt-rfp", "application/pdf"))
    storage["tgt-rfp"] = _node("tgt-rfp", "RFP source.pdf", "discarded-2",
                               size=555, mime="application/pdf")

    fake = FakeDrive(storage)
    _patch_drive(monkeypatch, fake)

    result = full_scan(db, project, user)
    assert result["folders"] == 2
    assert result["files"] == 2

    folders = {f.drive_id: f for f in db.query(Folder).filter(
        Folder.project_id == project.id).all()}
    files = {f.drive_id: f for f in db.query(File).filter(
        File.project_id == project.id).all()}

    # shortcut -> folder is stored as the target folder, re-parented where the
    # shortcut lives, and its children are discovered
    assert folders["tgt-draw"].parent_folder_id == "f-eleven"
    assert folders["tgt-draw"].path == "ELEVEN/03-Drawing"
    assert files["f-drw1"].path == "ELEVEN/03-Drawing/A-101.dwg"

    # shortcut -> file stored as its target file (shortcut's visible name kept)
    assert files["tgt-rfp"].mime_type == "application/pdf"
    assert files["tgt-rfp"].name == "RFP.pdf"
    assert files["tgt-rfp"].path == "ELEVEN/RFP.pdf"

    # no placeholder shortcut rows survive
    assert db.query(File).filter(File.project_id == project.id,
                                 File.mime_type == SHORTCUT).count() == 0
    # re-running is a clean idempotent rescan (old targets not duplicated)
    full_scan(db, project, user)
    folders2 = db.query(Folder).filter(Folder.project_id == project.id).count()
    files2 = db.query(File).filter(File.project_id == project.id).count()
    assert folders2 == len(folders)
    assert files2 == len(files)


def test_incremental_sync_detects_all_action_types(db, helper, monkeypatch):
    user = helper.make_user(db, "inc@example.com")
    project = helper.make_project(db, user)
    helper.seed_snapshot(db, project)
    _attach_token(db, user)
    from tests.fakes import FakeDrive

    fake = FakeDrive()
    _patch_drive(monkeypatch, fake)

    # 1) MODIFIED: BOQ.xlsx becomes bigger
    fake.apply_change("f-boq", lambda n: n.update({"size": "999999",
                                                   "modifiedTime": "2026-08-27T10:42:00Z"}))
    # 2) RENAMED: RFI-025.pdf -> RFI-026.pdf
    fake.apply_change("f-rfi", lambda n: n.update(
        {"name": "RFI-026.pdf", "modifiedTime": "2026-08-27T11:00:00Z"}))
    # 3) MOVED: dwg-001.dwg from Drawings to Reports
    fake.apply_change("f-dwg001", lambda n: n.update({"parents": ["f-reports"],
                                                      "modifiedTime": "2026-08-27T11:30:00Z"}))
    # 4) CREATED: new file in Correspondence
    fake.storage["f-rfi2"] = {
        "id": "f-rfi2", "name": "RFI-027.pdf", "mimeType": "application/pdf",
        "size": "2048", "createdTime": "2026-08-27T12:00:00Z",
        "modifiedTime": "2026-08-27T12:00:00Z", "trashed": False,
        "parents": ["f-corr"],
        "webViewLink": "https://drive.google.com/file/d/f-rfi2/view",
        "lastModifyingUser": None, "capabilities": {},
    }
    fake.apply_change("f-rfi2")
    # 5) REMOVED: RFI-026 disappears from the feed
    fake.apply_removal("f-rfi")

    result = incremental_sync(db, project, user)
    assert result["activities"] >= 5

    actions = [a.action for a in db.query(Activity).filter(
        Activity.project_id == project.id).order_by(Activity.id).all()]
    for expected in ("MODIFIED", "RENAMED", "MOVED", "CREATED", "TRASHED"):
        assert expected in actions, f"missing {expected} in {actions}"

    state = db.query(SyncState).filter(SyncState.project_id == project.id).first()
    assert state.start_page_token == "101"
    assert state.consecutive_errors == 0

    notifs = db.query(Notification).filter(
        Notification.project_id == project.id).all()
    assert len(notifs) >= 5


def test_incremental_sync_empty_feed_is_noop(db, helper, monkeypatch):
    user = helper.make_user(db, "emptyfeed@example.com")
    project = helper.make_project(db, user)
    helper.seed_snapshot(db, project)
    _attach_token(db, user)
    from tests.fakes import FakeDrive

    fake = FakeDrive()
    _patch_drive(monkeypatch, fake)

    result = incremental_sync(db, project, user)
    assert result["activities"] == 0
    assert db.query(Activity).filter(Activity.project_id == project.id).count() == 0


def test_sync_error_keeps_cursor(db, helper, monkeypatch):
    import pytest

    user = helper.make_user(db, "err@example.com")
    project = helper.make_project(db, user)
    helper.seed_snapshot(db, project)
    _attach_token(db, user)
    from tests.fakes import FakeDrive

    fake = FakeDrive()
    _patch_drive(monkeypatch, fake)

    def boom(page_token, page_size=1000):
        raise RuntimeError("simulated google failure")

    monkeypatch.setattr(fake, "list_changes", boom)

    with pytest.raises(RuntimeError):
        incremental_sync(db, project, user)

    state = db.query(SyncState).filter(SyncState.project_id == project.id).first()
    assert state.consecutive_errors == 1
    assert state.start_page_token == "100"