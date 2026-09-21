"""Unit tests for the real DriveClient's parameter handling."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.errors import GoogleNotFoundError
from app.services.google.drive import DriveClient


@pytest.fixture()
def captured(monkeypatch):
    calls = []

    def fake_request(method, url, *, params=None, json=None, files=None,
                     headers=None, timeout=None):
        calls.append({"url": url, "params": params or {}})
        return SimpleNamespace(status_code=200, text="", json=lambda: {"startPageToken": "t"})

    monkeypatch.setattr("app.services.google.drive.httpx.request", fake_request)
    return calls


def test_start_page_token_has_no_include_items_from_all_drives(captured):
    client = DriveClient("tok")
    assert client.get_start_page_token() == "t"
    params = captured[-1]["params"]
    assert params.get("supportsAllDrives") == "true"
    assert "includeItemsFromAllDrives" not in params, params


def test_list_children_keeps_include_items_from_all_drives(captured):
    client = DriveClient("tok")
    client.list_children("f-1")
    params = captured[-1]["params"]
    assert params.get("supportsAllDrives") == "true"
    assert params.get("includeItemsFromAllDrives") == "true"


def test_list_changes_keeps_include_items_from_all_drives(monkeypatch):
    calls = []

    def fake_request(method, url, *, params=None, json=None, files=None,
                     headers=None, timeout=None):
        calls.append({"url": url, "params": params or {}})
        return SimpleNamespace(status_code=200, text="",
                               json=lambda: {"nextPageToken": None,
                                             "changes": [], "newStartPageToken": "n"})

    monkeypatch.setattr("app.services.google.drive.httpx.request", fake_request)
    client = DriveClient("tok")
    client.list_changes("abc")
    params = calls[-1]["params"]
    assert params.get("supportsAllDrives") == "true"
    assert params.get("includeItemsFromAllDrives") == "true"


def test_list_changes_fields_are_valid_for_drive_v3(captured):
    """changes.list must not request invalid Change fields (id/type)."""
    client = DriveClient("tok")
    client.list_changes("abc")
    fields = captured[-1]["params"].get("fields", "")
    assert "changeType" in fields
    assert "fileId" in fields
    assert "newStartPageToken" in fields
    assert "changes(id" not in fields
    assert "changes(type" not in fields
    assert ",type," not in fields
    assert "nextStartPageToken" not in fields


def test_get_file_404_raises_google_not_found(monkeypatch):
    def fake_request(method, url, *, params=None, json=None, files=None,
                     headers=None, timeout=None):
        return SimpleNamespace(status_code=404, text="File not found",
                               json=lambda: {})

    monkeypatch.setattr("app.services.google.drive.httpx.request", fake_request)
    client = DriveClient("tok")
    with pytest.raises(GoogleNotFoundError):
        client.get_file("nope-id")