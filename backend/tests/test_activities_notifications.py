"""Activities timeline and notification center."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Activity, Notification, Project, User
from app.services.activity import record_activity
from app.services.notification_service import build_notification


def _seed_activities(db, project: Project, user: User) -> list[Activity]:
    acts = [
        record_activity(db, project, user, "CREATED", "FILE", "RFI-025.pdf",
                        drive_id="f-rfi-oid", file_path="Correspondence/RFI-025.pdf",
                        folder_path="Correspondence", details={"ext": "pdf"},
                        actor_email=user.email, actor_name=user.name,
                        detected_at=datetime(2026, 8, 27, 10, 42, tzinfo=timezone.utc)),
        record_activity(db, project, user, "MODIFIED", "FILE", "BOQ.xlsx",
                        drive_id="f-boq-oid", file_path="Technical/BOQ.xlsx",
                        folder_path="Technical", details={"ext": "xlsx"},
                        actor_email=user.email, actor_name=user.name,
                        detected_at=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)),
    ]
    db.commit()
    return acts


def test_activity_listing_and_filters(client, db, helper):
    owner = helper.make_user(db, "al@example.com")
    project = helper.make_project(db, owner)
    helper.seed_snapshot(db, project)
    _seed_activities(db, project, owner)
    helper.login_as(client, owner)

    resp = client.get(f"/api/projects/{project.id}/activities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2

    resp = client.get(f"/api/projects/{project.id}/activities?action=created")
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["target_name"] == "RFI-025.pdf"

    resp = client.get(f"/api/projects/{project.id}/activities?date_from=2026-08-27T11:00:00Z")
    assert resp.json()["total"] == 1

    resp = client.get(f"/api/projects/{project.id}/activities?q=boq")
    assert resp.json()["total"] == 1


def test_notification_flow(client, db, helper):
    owner = helper.make_user(db, "nowner@example.com")
    viewer = helper.make_user(db, "nviewer@example.com")
    project = helper.make_project(db, owner)
    from app.models import ProjectMember

    db.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="VIEWER",
                         added_by=owner.id))
    db.commit()
    acts = _seed_activities(db, project, owner)

    helper.login_as(client, viewer)
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["unread"] == 2

    resp = client.get("/api/notifications/unread-count")
    assert resp.json()["total"] == 2

    nid = body["items"][0]["id"]
    resp = client.post(f"/api/notifications/{nid}/read")
    assert resp.status_code == 200
    assert resp.json()["is_read"] is True

    resp = client.get("/api/notifications/unread-count")
    assert resp.json()["total"] == 1

    resp = client.post(f"/api/notifications/read-all?project_id={project.id}")
    assert resp.status_code == 204
    resp = client.get("/api/notifications/unread-count")
    assert resp.json()["total"] == 0


def test_activity_users_dropdown(client, db, helper):
    owner = helper.make_user(db, "adrop@example.com")
    project = helper.make_project(db, owner)
    _seed_activities(db, project, owner)
    helper.login_as(client, owner)
    resp = client.get(f"/api/projects/{project.id}/activities/users")
    assert resp.status_code == 200
    emails = [u["key"] for u in resp.json()]
    assert "adrop@example.com" in emails


def test_notification_prefs_mute_filters_delivery(client, db, helper):
    from app.models import Notification as N, ProjectMember
    from app.services.activity import record_activity

    owner = helper.make_user(db, "pmute-owner@example.com")
    member = helper.make_user(db, "pmute-member@example.com")
    project = helper.make_project(db, owner)
    db.add(ProjectMember(project_id=project.id, user_id=member.id, role="VIEWER",
                         added_by=owner.id))
    db.commit()

    def act(name, drive_id):
        a = record_activity(db, project, owner, "CREATED", "FILE", name,
                            drive_id=drive_id, file_path=f"Tech/{name}",
                            folder_path="Tech", actor_email=owner.email,
                            actor_name=owner.name)
        db.commit()
        return a

    helper.login_as(client, member)
    resp = client.patch(f"/api/notifications/prefs/{project.id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = client.get("/api/notifications/prefs")
    assert resp.status_code == 200
    proj = next(i for i in resp.json()["items"] if i["project_id"] == project.id)
    assert proj["enabled"] is False
    assert proj["project_name"] == "ATIKA"

    a1 = act("RFI-099.pdf", "f-rfi-099")
    ids1 = [n.user_id for n in db.query(N).filter(N.activity_id == a1.id).all()]
    assert owner.id in ids1
    assert member.id not in ids1

    resp = client.patch(f"/api/notifications/prefs/{project.id}", json={"enabled": True})
    assert resp.json()["enabled"] is True
    a2 = act("BOQ-200.xlsx", "f-boq-200")
    ids2 = [n.user_id for n in db.query(N).filter(N.activity_id == a2.id).all()]
    assert member.id in ids2

    resp = client.patch("/api/notifications/prefs/99999", json={"enabled": False})
    assert resp.status_code in (403, 404)