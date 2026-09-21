"""Pytest harness. Must set env vars before importing app modules."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ["MONITOR_ENABLED"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key-for-ctests-only"
os.environ["APP_ENV"] = "test"
os.environ["SYNC_INTERVAL_SECONDS"] = "1"
_tmpdir = tempfile.mkdtemp(prefix="drivedoc-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmpdir, 'test.db').as_posix()}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Activity, File, Folder, Project, ProjectMember, SyncState, User,
)
from app.security import create_session_token  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(scope="session")
def _client():
    return TestClient(app)


@pytest.fixture()
def client(_client: TestClient):
    _client.cookies.clear()
    return _client


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_user(db, email: str, role: str = "VIEWER", name: str = "Test User") -> User:
    user = User(email=email, name=name, role=role, google_sub=f"sub-{email}", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login_as(client: TestClient, user: User) -> None:
    token = create_session_token(user.id, user.google_sub, user.role)
    client.cookies.set(settings.TOKEN_COOKIE_NAME, token)


def make_project(db, user: User, *, folder_id: str = "root-abc",
                 name: str = "ATIKA", creator_role="PROJECT_MANAGER") -> Project:
    project = Project(name=name, google_folder_id=folder_id, created_by=user.id, status="ACTIVE")
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=creator_role,
                         added_by=user.id))
    db.add(SyncState(project_id=project.id, start_page_token="100"))
    db.commit()
    db.refresh(project)
    return project


def seed_snapshot(db, project: Project, drive_ids: list[str] | None = None) -> None:
    """Insert a standard folder/file snapshot for a project."""
    parents = {
        "f-drawings": "root-abc", "f-technical": "root-abc",
        "f-civil": "f-technical", "f-reports": "root-abc", "f-corr": "root-abc",
    }
    for fid in parents:
        db.add(Folder(drive_id=fid, project_id=project.id,
                      name=fid.replace("f-", "").title(),
                      path=fid.replace("f-", "").title(),
                      parent_folder_id=parents[fid], depth=1, trashed=False))
    db.add(File(drive_id="f-dwg001", project_id=project.id, name="dwg-001.dwg",
                mime_type="file/plain", extension="dwg", size=51200,
                folder_drive_id="f-drawings",
                path="Drawings/dwg-001.dwg", trashed=False))
    db.add(File(drive_id="f-boq", project_id=project.id, name="BOQ.xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                extension="xlsx", size=204800, folder_drive_id="f-technical",
                path="Technical/BOQ.xlsx", trashed=False))
    db.add(File(drive_id="f-rfi", project_id=project.id, name="RFI-025.pdf",
                mime_type="application/pdf", extension="pdf", size=102400,
                folder_drive_id="f-corr", path="Correspondence/RFI-025.pdf", trashed=False))
    db.commit()


class Helper:
    make_user = staticmethod(make_user)
    login_as = staticmethod(login_as)
    make_project = staticmethod(make_project)
    seed_snapshot = staticmethod(seed_snapshot)


@pytest.fixture()
def helper():
    return Helper()