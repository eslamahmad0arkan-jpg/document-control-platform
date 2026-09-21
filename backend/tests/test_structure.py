"""Project structure (WBS / document tree) endpoints.

Focuses on the subtree behaviour, especially DELETE — a node must take its
entire descendant subtree (and their links) with it and orphan nothing.
Google Drive stays the source of truth, so no Drive fakes are required here.
"""
from __future__ import annotations

import pytest

from app.models import (
    ProjectStructureLink,
    ProjectStructureNode,
    User,
)

API = "/api/projects"


def _auth(client, helper, db) -> User:
    user = helper.make_user(db, "struct-owner@example.com",
                            role="PROJECT_MANAGER")
    helper.login_as(client, user)
    return user


def _mk(client, helper, db):
    user = _auth(client, helper, db)
    project = helper.make_project(db, user, folder_id="root-structure-x")
    return user, project


def _create(client, project_id: int, name: str, **kw) -> dict:
    resp = client.post(f"{API}/{project_id}/structure/nodes",
                       json={"name": name, **kw})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _node_ids_recursive(flat: list[dict]) -> list[int]:
    out: list[int] = []
    for n in flat:
        out.append(n["id"])
        out.extend(_node_ids_recursive(n["children"]))
    return out


def _ids(children) -> list[int]:
    """children are returned as plain int ids by the detail endpoint."""
    return list(children)


# --- create / tree ---------------------------------------------------------


def test_create_nodes_build_nested_tree(client, helper, db):
    user, project = _mk(client, helper, db)
    root = _create(client, project.id, "WBS", code="1")
    child = _create(client, project.id, "Discipline", parent_id=root["id"], code="1.1")
    leaf = _create(client, project.id, "Area", parent_id=child["id"], code="1.1.1")

    resp = client.get(f"{API}/{project.id}/structure/tree")
    assert resp.status_code == 200
    tree = resp.json()
    assert len(tree) == 1 and tree[0]["id"] == root["id"]
    assert [c["id"] for c in tree[0]["children"]] == [child["id"]]
    assert [c["id"] for c in tree[0]["children"][0]["children"]] == [leaf["id"]]


def test_tree_root_children_parent_ids(client, helper, db):
    user, project = _mk(client, helper, db)
    a = _create(client, project.id, "A", code="1")
    b = _create(client, project.id, "B", parent_id=a["id"], code="1.1")
    c = _create(client, project.id, "C", code="2")

    roots = client.get(f"{API}/{project.id}/structure/nodes").json()
    assert {r["name"] for r in roots} == {"A", "C"}

    detail = client.get(f"{API}/{project.id}/structure/nodes/{a['id']}").json()
    children = detail["children"]
    assert children == [b["id"]]


def test_create_node_duplicate_code_conflict(client, helper, db):
    user, project = _mk(client, helper, db)
    _create(client, project.id, "First", code="WBS-10")
    dup = client.post(f"{API}/{project.id}/structure/nodes",
                      json={"name": "Second", "code": "WBS-10"})
    assert dup.status_code == 409


def test_update_node_rename_and_mark_delete(client, helper, db):
    user, project = _mk(client, helper, db)
    node = _create(client, project.id, "Old Name", code="X1")
    upd = client.patch(f"{API}/{project.id}/structure/nodes/{node['id']}",
                       json={"name": "New Name", "description": "renamed"})
    assert upd.status_code == 200, upd.text
    body = upd.json()
    assert body["name"] == "New Name"
    assert body["description"] == "renamed"


# --- subtree delete --------------------------------------------------------

def test_delete_root_cascades_to_all_descendants(client, helper, db):
    user, project = _mk(client, helper, db)

    def tree(name: str, parent: int | None, depth: int = 0) -> tuple[int, list[int]]:
        node = _create(client, project.id, name, parent_id=parent)
        kids = [node["id"]]
        if depth < 6:
            for i in range(2):
                kids += tree(f"{name}.{i}", node["id"], depth + 1)[1]
        return node["id"], kids

    root_id, _ = tree("ROOT", None)

    resp = client.delete(f"{API}/{project.id}/structure/nodes/{root_id}")
    assert resp.status_code == 204

    remaining = (
        db.query(ProjectStructureNode)
        .filter(ProjectStructureNode.project_id == project.id)
        .count()
    )
    assert remaining == 0


def test_delete_leaf_leaves_siblings(client, helper, db):
    user, project = _mk(client, helper, db)
    root = _create(client, project.id, "ROOT")
    left = _create(client, project.id, "Left", parent_id=root["id"], position=0)
    right = _create(client, project.id, "Right", parent_id=root["id"], position=1)

    resp = client.delete(f"{API}/{project.id}/structure/nodes/{left['id']}")
    assert resp.status_code == 204

    tree = client.get(f"{API}/{project.id}/structure/tree").json()
    tree_ids = _node_ids_recursive(tree)
    assert root["id"] in tree_ids and right["id"] in tree_ids
    assert left["id"] not in tree_ids


def test_delete_root_removes_links_of_descendants(client, helper, db):
    user, project = _mk(client, helper, db)
    parent = _create(client, project.id, "Parent")
    child = _create(client, project.id, "Child", parent_id=parent["id"])

    link_payload = {
        "drive_id": "drive-file-000", "doc_number": "DOC-1", "wbs_code": "P.1",
        "revision": "A", "status": "DRAFT",
    }
    link = client.post(f"{API}/{project.id}/structure/nodes/{child['id']}/links",
                       json=link_payload)
    assert link.status_code == 201, link.text

    resp = client.delete(f"{API}/{project.id}/structure/nodes/{parent['id']}")
    assert resp.status_code == 204

    leftover_links = (
        db.query(ProjectStructureLink)
        .filter(ProjectStructureLink.project_id == project.id)
        .count()
    )
    assert leftover_links == 0


# --- permissions ----------------------------------------------------------


def test_viewer_cannot_create_structure_node(client, helper, db):
    user = helper.make_user(db, "struct-viewer@example.com", role="VIEWER")
    owner = helper.make_user(db, "struct-owner2@example.com", role="PROJECT_MANAGER")
    project = helper.make_project(db, owner, folder_id="root-structure-z")
    helper.login_as(client, user)

    resp = client.post(f"{API}/{project.id}/structure/nodes",
                       json={"name": "Nope"})
    assert resp.status_code == 403
