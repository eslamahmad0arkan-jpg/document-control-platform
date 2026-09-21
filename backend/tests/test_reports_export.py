"""Reports aggregation and CSV/Excel/PDF export."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Activity, File, Project, User
from app.services.activity import record_activity
from app.services.report_service import build_reports


def _seed(client, db, helper):
    owner = helper.make_user(db, "rpt@example.com")
    project = helper.make_project(db, owner)
    helper.seed_snapshot(db, project)
    record_activity(db, project, owner, "CREATED", "FILE", "N1.pdf",
                    drive_id="x1", file_path="A/N1.pdf", folder_path="A",
                    detected_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc))
    record_activity(db, project, owner, "CREATED", "FILE", "N2.xlsx",
                    drive_id="x2", file_path="A/N2.xlsx", folder_path="A",
                    detected_at=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc))
    record_activity(db, project, owner, "MODIFIED", "FILE", "BOQ.xlsx",
                    drive_id="x3", file_path="Technical/BOQ.xlsx", folder_path="Technical",
                    detected_at=datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc))
    db.commit()
    helper.login_as(client, owner)
    return project, owner


def test_reports_aggregation(db, helper):
    owner = helper.make_user(db, "rpt2@example.com")
    project = helper.make_project(db, owner)
    helper.seed_snapshot(db, project)
    record_activity(db, project, owner, "CREATED", "FILE", "Z1.pdf",
                    drive_id="y1", file_path="A/Z1.pdf", folder_path="A",
                    detected_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc))
    db.commit()

    report = build_reports(db, project.id)
    assert report["overview"]["total_files"] == 3
    assert report["overview"]["total_folders"] == 5
    assert report["overview"]["total_activities"] == 1
    assert report["overview"]["active_users"] == 1
    assert report["overview"]["total_size"] == 51200 + 204800 + 102400
    assert report["activity"]["by_action"]
    assert report["files"]["by_extension"]
    assert report["files"]["recent_files"]


def test_reports_endpoint(client, db, helper):
    project, owner = _seed(client, db, helper)
    resp = client.get(f"/api/projects/{project.id}/reports?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overview"]["new_files"] == 2
    assert body["overview"]["modified_files"] == 1


def test_reports_date_range(client, db, helper):
    project, _ = _seed(client, db, helper)
    resp = client.get(
        f"/api/projects/{project.id}/reports"
        "?date_from=2026-08-21T00:00:00Z&date_to=2026-08-23T00:00:00Z"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overview"]["new_files"] == 1   # only N2.xlsx
    assert body["overview"]["total_activities"] == 2


def test_export_csv(client, db, helper):
    project, _ = _seed(client, db, helper)
    resp = client.get(f"/api/projects/{project.id}/reports/export?format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert b"Project Report" in resp.content
    assert resp.content.startswith(b"\xef\xbb\xbf")  # utf-8 BOM for Excel


def test_export_xlsx(client, db, helper):
    project, _ = _seed(client, db, helper)
    resp = client.get(f"/api/projects/{project.id}/reports/export?format=xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats")
    assert resp.content[:4] == b"PK\x03\x04"  # zip magic


def test_export_pdf(client, db, helper):
    project, _ = _seed(client, db, helper)
    resp = client.get(f"/api/projects/{project.id}/reports/export?format=pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"