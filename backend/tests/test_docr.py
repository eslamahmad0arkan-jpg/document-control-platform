"""Document-control: file locks (check-out/in) and comments."""
from __future__ import annotations

from app.models import Notification, ProjectMember


def _seed(client, db, helper, member_role="EDITOR"):
    owner = helper.make_user(db, "docr-owner@example.com")
    member = helper.make_user(db, "docr-member@example.com")
    project = helper.make_project(db, owner)
    db.add(ProjectMember(project_id=project.id, user_id=member.id,
                         role=member_role, added_by=owner.id))
    helper.seed_snapshot(db, project)
    db.commit()
    return project, owner, member


def test_lock_unlock_flow(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)

    lock_resp = client.post(f"/api/projects/{project.id}/files/f-dwg001/lock",
                            json={"comment": "Revising for IFC"})
    assert lock_resp.status_code == 200
    body = lock_resp.json()
    assert body["drive_id"] == "f-dwg001"
    assert body["locked_by_email"] == "docr-member@example.com"
    assert body["released_at"] is None

    # Locking again conflicts
    again = client.post(f"/api/projects/{project.id}/files/f-dwg001/lock",
                        json={"comment": "dup"})
    assert again.status_code == 409

    got = client.get(f"/api/projects/{project.id}/files/f-dwg001/lock")
    assert got.status_code == 200
    assert got.json()["locked_by_email"] == "docr-member@example.com"

    unlock = client.post(f"/api/projects/{project.id}/files/f-dwg001/unlock")
    assert unlock.status_code == 200
    assert unlock.json()["released_at"] is not None

    got2 = client.get(f"/api/projects/{project.id}/files/f-dwg001/lock")
    assert got2.json() is None

    # Unlocking an unlocked file is a 404
    again2 = client.post(f"/api/projects/{project.id}/files/f-dwg001/unlock")
    assert again2.status_code == 404


def test_locked_file_appears_in_explorer(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)
    client.post(f"/api/projects/{project.id}/files/f-dwg001/lock",
                json={"comment": "checked out"})
    resp = client.get(f"/api/projects/{project.id}/explorer?folder_id=f-drawings&sort=name")
    assert resp.status_code == 200
    locked = [f for f in resp.json()["files"] if f["drive_id"] == "f-dwg001"]
    assert locked and locked[0]["lock"]["locked_by_email"] == "docr-member@example.com"


def test_viewer_cannot_lock(client, db, helper):
    project, owner, member = _seed(client, db, helper, member_role="VIEWER")
    helper.login_as(client, member)
    resp = client.post(f"/api/projects/{project.id}/files/f-dwg001/lock",
                       json={"comment": "nope"})
    assert resp.status_code == 403


def test_comment_crud_and_notification(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)

    resp = client.post(f"/api/projects/{project.id}/files/f-dwg001/comments",
                       json={"body": "Please move hatch to Layer 12"})
    assert resp.status_code == 201
    cid = resp.json()["id"]
    assert resp.json()["user_email"] == "docr-member@example.com"

    listing = client.get(f"/api/projects/{project.id}/files/f-dwg001/comments")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["body"].startswith("Please move")

    # Commenter got no self-notification; owner did.
    helper.login_as(client, owner)
    notifs = client.get("/api/notifications?per_page=50").json()["items"]
    assert any(n["type"] == "file_commented" and n["title"].endswith("dwg-001.dwg")
               for n in notifs)

    # Owner is the manager → allowed to delete anyone's comment.
    resp = client.delete(f"/api/projects/{project.id}/comments/{cid}")
    assert resp.status_code == 204
    assert len(client.get(f"/api/projects/{project.id}/files/f-dwg001/comments").json()) == 0


def test_viewer_cannot_delete_others_comment(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    viewer = helper.make_user(db, "docr-viewer@example.com")
    db.add(ProjectMember(project_id=project.id, user_id=viewer.id,
                         role="VIEWER", added_by=owner.id))
    db.commit()
    helper.login_as(client, member)
    cid = client.post(f"/api/projects/{project.id}/files/f-dwg001/comments",
                      json={"body": "markup"}).json()["id"]
    helper.login_as(client, viewer)
    assert client.delete(f"/api/projects/{project.id}/comments/{cid}").status_code == 403
    #   but the author can
    helper.login_as(client, member)
    assert client.delete(f"/api/projects/{project.id}/comments/{cid}").status_code == 204


def test_locks_list_active_and_history(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)
    client.post(f"/api/projects/{project.id}/files/f-dwg001/lock", json={})
    client.post(f"/api/projects/{project.id}/files/f-dwg001/unlock")

    active = client.get(f"/api/projects/{project.id}/locks?state=active").json()
    assert all(l["released_at"] is None for l in active)

    history = client.get(f"/api/projects/{project.id}/locks?state=released").json()
    assert any(l["drive_id"] == "f-dwg001" and l["released_at"] for l in history)


# --- threaded & general comments -------------------------------------------

def test_threaded_replies_and_reply_count(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)

    top = client.post(f"/api/projects/{project.id}/files/f-dwg001/comments",
                      json={"body": "Top-level markup"}).json()
    reply = client.post(f"/api/projects/{project.id}/files/f-dwg001/comments",
                        json={"body": "Reply to markup", "parent_id": top["id"]})
    assert reply.status_code == 201
    assert reply.json()["parent_id"] == top["id"]

    # Replies one level deep only
    deep = client.post(f"/api/projects/{project.id}/files/f-dwg001/comments",
                       json={"body": "too deep", "parent_id": reply.json()["id"]})
    assert deep.status_code == 409

    listing = client.get(f"/api/projects/{project.id}/files/f-dwg001/comments").json()
    assert len(listing) == 1  # only top-level comments
    assert listing[0]["reply_count"] == 1

    replies = client.get(f"/api/projects/{project.id}/comments/{top['id']}/replies").json()
    assert len(replies) == 1 and replies[0]["parent_id"] == top["id"]


def test_mentions_notify_targeted_user(client, db, helper):
    from app.models import Notification
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)

    resp = client.post(f"/api/projects/{project.id}/files/f-dwg001/comments",
                       json={"body": "@docr-owner@example.com please review",
                             "mention_ids": [owner.id]})
    assert resp.status_code == 201
    assert owner.id in resp.json()["mentions"]

    helper.login_as(client, owner)
    notifs = client.get("/api/notifications?per_page=50").json()["items"]
    assert any(n["type"] == "mention" and n["title"] == "You were mentioned in a comment"
               for n in notifs)

    # Mentions are also extracted from @email in the body automatically
    helper.login_as(client, member)
    auto = client.post(f"/api/projects/{project.id}/files/f-dwg001/comments",
                       json={"body": "cc @docr-member@example.com - thanks"})
    assert auto.status_code == 201
    assert member.id in auto.json()["mentions"]


def test_general_project_discussion(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)

    post = client.post(f"/api/projects/{project.id}/comments",
                       json={"body": "General project note"})
    assert post.status_code == 201
    assert post.json()["drive_id"] == ""

    reply = client.post(f"/api/projects/{project.id}/comments",
                        json={"body": "agree", "parent_id": post.json()["id"]})
    assert reply.status_code == 201

    listing = client.get(f"/api/projects/{project.id}/comments").json()
    assert len(listing) == 1 and listing[0]["body"] == "General project note"


def test_comment_attachment_upload_and_download(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)
    cid = client.post(f"/api/projects/{project.id}/files/f-dwg001/comments",
                      json={"body": "see attachment"}).json()["id"]

    resp = client.post(
        f"/api/projects/{project.id}/comments/{cid}/attachments",
        files={"file": ("layout.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 201
    att = resp.json()
    assert att["filename"] == "layout.pdf"
    assert att["size"] == len(b"%PDF-1.4 fake")

    # Attachment appears in comment listing
    listing = client.get(f"/api/projects/{project.id}/files/f-dwg001/comments").json()
    assert len(listing[0]["attachments"]) == 1

    dl = client.get(f"/api/projects/{project.id}/comments/{cid}/attachments/{att['id']}/download")
    assert dl.status_code == 200
    assert dl.content == b"%PDF-1.4 fake"


def test_comment_attachment_allowed_for_viewer(client, db, helper):
    project, owner, member = _seed(client, db, helper, member_role="VIEWER")
    helper.login_as(client, member)
    cid = client.post(f"/api/projects/{project.id}/files/f-dwg001/comments",
                      json={"body": "viewer note"}).json()["id"]
    resp = client.post(f"/api/projects/{project.id}/comments/{cid}/attachments",
                       files={"file": ("x.txt", b"x", "text/plain")})
    assert resp.status_code == 201


# --- approvals ---------------------------------------------------------------

def test_approval_workflow(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)

    req = client.post(f"/api/projects/{project.id}/files/f-dwg001/approvals",
                      json={"reviewer_user_id": owner.id, "comment": "Approve IFC set"})
    assert req.status_code == 201
    appr = req.json()
    assert appr["status"] == "PENDING"
    assert appr["reviewer_name"]

    # approving with a decision
    helper.login_as(client, owner)
    decided = client.post(f"/api/projects/{project.id}/approvals/{appr['id']}/decide",
                          json={"status": "APPROVED", "comment": "Looks good"})
    assert decided.status_code == 200
    assert decided.json()["status"] == "APPROVED"
    assert decided.json()["decided_comment"] == "Looks good"
    assert decided.json()["decided_at"] is not None

    # Cannot decide twice
    again = client.post(f"/api/projects/{project.id}/approvals/{appr['id']}/decide",
                        json={"status": "REJECTED"})
    assert again.status_code == 409

    listing = client.get(f"/api/projects/{project.id}/approvals?status=approved").json()
    assert any(a["id"] == appr["id"] and a["status"] == "APPROVED" for a in listing)


def test_approval_decision_requires_reviewer(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    third = helper.make_user(db, "docr-third@example.com")
    db.add(ProjectMember(project_id=project.id, user_id=third.id,
                         role="VIEWER", added_by=owner.id))
    db.commit()
    helper.login_as(client, member)
    appr = client.post(f"/api/projects/{project.id}/files/f-dwg001/approvals",
                       json={"reviewer_user_id": owner.id}).json()
    # the requesting member is not the reviewer nor a manager → 403
    resp = client.post(f"/api/projects/{project.id}/approvals/{appr['id']}/decide",
                       json={"status": "APPROVED"})
    assert resp.status_code == 403
    # another viewer isn't allowed either
    helper.login_as(client, third)
    resp = client.post(f"/api/projects/{project.id}/approvals/{appr['id']}/decide",
                       json={"status": "APPROVED"})
    assert resp.status_code == 403
    # the actual reviewer (owner = manager) is allowed
    helper.login_as(client, owner)
    resp = client.post(f"/api/projects/{project.id}/approvals/{appr['id']}/decide",
                       json={"status": "APPROVED", "comment": "ok"})
    assert resp.status_code == 200 and resp.json()["status"] == "APPROVED"


def test_approval_reviewer_must_be_member(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    outsider = helper.make_user(db, "docr-outsider@example.com")
    db.commit()
    helper.login_as(client, member)
    resp = client.post(f"/api/projects/{project.id}/files/f-dwg001/approvals",
                       json={"reviewer_user_id": outsider.id})
    assert resp.status_code == 404
    # self-approval is blocked
    resp2 = client.post(f"/api/projects/{project.id}/files/f-dwg001/approvals",
                        json={"reviewer_user_id": member.id})
    assert resp2.status_code == 409


# --- trash bin ---------------------------------------------------------------

def test_trash_list_and_restore(client, db, helper):
    project, owner, member = _seed(client, db, helper)
    helper.login_as(client, member)
    trash = client.get(f"/api/projects/{project.id}/trash")
    assert trash.status_code == 200
    # seed_snapshot leaves nothing trashed
    assert trash.json()["files"] == [] and trash.json()["folders"] == []

    # mark one file trashed locally + via the API requires Drive; emulate with DB
    from app.models import File
    f = db.query(File).filter(File.drive_id == "f-dwg001",
                              File.project_id == project.id).first()
    f.trashed = True
    db.commit()

    trash2 = client.get(f"/api/projects/{project.id}/trash").json()
    assert any(x["drive_id"] == "f-dwg001" for x in trash2["files"])