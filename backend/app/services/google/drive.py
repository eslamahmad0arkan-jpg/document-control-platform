"""Google Drive API client (Drive REST API v3).

Caps realities of the API:
- changes.list yields lightweight change notifications; we diff against our snapshot
  to infer CREATED / MODIFIED / MOVED / RENAMED / TRASHED.
- Modified-by is read from the `lastModifyingUser` field when returned by Google.
- folders whose contents are affected surface via their own change record; the sync
  engine re-discovers their subtree.
- All failures map to typed exceptions (see errors.py); nothing crashes the app.
"""
from __future__ import annotations

import io
import time
from typing import Iterator

import httpx

from ...config import settings
from ...errors import (
    GoogleAuthExpiredError,
    GoogleConnectionError,
    GoogleNotFoundError,
    GooglePermissionError,
    GoogleRateLimitError,
)

DRIVE_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

BASE_FIELDS = (
    "id,name,mimeType,size,modifiedTime,createdTime,trashed,parents,webViewLink,"
    "shortcutDetails(targetId,targetMimeType),"
    "lastModifyingUser(displayName,emailAddress),capabilities,trashingUser(displayName,emailAddress),trashedTime"
)
LIST_FIELDS = (
    "nextPageToken,files("
    "id,name,mimeType,size,modifiedTime,createdTime,trashed,parents,webViewLink,"
    "shortcutDetails(targetId,targetMimeType),"
    "lastModifyingUser(displayName,emailAddress),capabilities"
    ")"
)
CHANGES_FIELDS = (
    "nextPageToken,newStartPageToken,changes("
    "fileId,changeType,time,removed,file("
    "id,name,mimeType,size,modifiedTime,trashed,parents,webViewLink,"
    "shortcutDetails(targetId,targetMimeType)"
    "))"
)


def _quote(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _is_429_text(text: str) -> bool:
    lowered = text.lower()
    return "rateLimitExceeded" in lowered or "userRateLimitExceeded" in lowered


class DriveClient:
    """Stateless-safe client bound to one user's credential. `refresh_cb` returns
    a fresh access token string when the current one is invalid."""

    def __init__(self, access_token: str, refresh_cb=None):
        self._token = access_token
        self._refresh_cb = refresh_cb

    # -- low level ----------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, url: str, params: dict | None = None,
                 json_body: dict | None = None, files=None, retries: int = 2) -> dict:
        return self._perform(method, url, params=params, json_body=json_body,
                             files=files, retries=retries).json()

    def _perform(self, method: str, url: str, *, params: dict | None = None,
                 json_body: dict | None = None, files=None, retries: int = 2) -> httpx.Response:
        params = dict(params or {})
        # supportsAllDrives is accepted by every Drive API endpoint; but
        # includeItemsFromAllDrives is only valid on files.list / changes.list,
        # so callers add it explicitly where relevant (see list_children/list_changes).
        params.setdefault("supportsAllDrives", "true")
        for attempt in range(retries + 1):
            try:
                resp = httpx.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    files=files,
                    headers=self._headers(),
                    timeout=60,
                )
            except httpx.HTTPError as exc:
                if attempt < retries:
                    time.sleep(self._backoff(attempt))
                    continue
                raise GoogleConnectionError(details={"cause": str(exc)}) from exc

            if resp.status_code == 401 and self._refresh_cb is not None:
                self._token = self._refresh_cb()
                if self._token:
                    continue  # retry same attempt slot with a fresh token
                raise GoogleAuthExpiredError()

            if resp.status_code == 429 or (resp.status_code >= 500 and _is_429_text(resp.text)):
                if attempt < retries:
                    time.sleep(self._backoff(attempt))
                    continue
                raise GoogleRateLimitError(details={"body": resp.text[:300]})

            if resp.status_code in (502, 503, 504):
                if attempt < retries:
                    time.sleep(self._backoff(attempt))
                    continue
                raise GoogleConnectionError(details={"http": resp.status_code})

            if resp.status_code != 200:
                self._raise_api_error(resp.status_code, resp.text)
            return resp

        raise GoogleConnectionError("Request failed after retries.")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(settings.SYNC_RETRY_BASE_SECONDS * (2 ** attempt), settings.SYNC_MAX_BACKOFF_SECONDS)

    @staticmethod
    def _raise_api_error(status: int, text: str) -> None:
        details = {"http": status, "body": text[:500], "message": text[:300]}
        if status == 401:
            raise GoogleAuthExpiredError(details)
        if status == 403:
            raise GooglePermissionError(details)
        if status == 404:
            raise GoogleNotFoundError(details)
        raise GoogleConnectionError(f"Google API error HTTP {status}.", details)

    # -- metadata -----------------------------------------------------------

    def get_file(self, file_id: str) -> dict:
        return self._request(
            "GET",
            f"{DRIVE_BASE}/files/{file_id}",
            params={"fields": BASE_FIELDS},
        )

    def get_media(self, file_id: str, export_mime: str | None = None) -> tuple[bytes, str]:
        """Download raw file bytes (or export a Google-native document to another
        format) and return (content, content-type). Uses the bound user's token."""
        if export_mime:
            resp = self._perform(
                "GET", f"{DRIVE_BASE}/files/{file_id}/export", params={"mimeType": export_mime}
            )
        else:
            resp = self._perform("GET", f"{DRIVE_BASE}/files/{file_id}", params={"alt": "media"})
        content_type = resp.headers.get("content-type", "application/octet-stream").split(";")[0]
        return resp.content, content_type

    def list_children(self, folder_id: str, *, trashed: bool = True,
                      order_by: str | None = None) -> list[dict]:
        q = f"{_quote(folder_id)} in parents"
        if not trashed:
            q += " and trashed = false"
        params = {
            "q": q,
            "pageSize": "1000",
            "fields": LIST_FIELDS,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if order_by:
            params["orderBy"] = order_by
        out: list[dict] = []
        page_token = None
        while True:
            if page_token:
                params["pageToken"] = page_token
            elif params.get("pageToken"):
                del params["pageToken"]
            data = self._request("GET", f"{DRIVE_BASE}/files", params=params)
            out.extend(data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return out

    # -- mutations ----------------------------------------------------------

    def create_folder(self, parent_id: str | None, name: str) -> dict:
        body = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        return self._request("POST", f"{DRIVE_BASE}/files",
                             params={"fields": "id,name,mimeType,parents,webViewLink"},
                             json_body=body)

    def upload_file(self, parent_id: str, name: str, content: bytes, mime: str) -> dict:
        metadata = {"name": name, "parents": [parent_id]}
        files = {
            "metadata": (None, __import__("json").dumps(metadata),
                         "application/json; charset=UTF-8"),
            "file": (name, io.BytesIO(content), mime or "application/octet-stream"),
        }
        return self._request(
            "POST", f"{UPLOAD_BASE}/files",
            params={"uploadType": "multipart", "fields": "id,name,mimeType,parents,size,webViewLink"},
            files=files,
        )

    def rename(self, file_id: str, new_name: str) -> dict:
        return self._request("PATCH", f"{DRIVE_BASE}/files/{file_id}",
                             params={"fields": "id,name,modifiedTime"},
                             json_body={"name": new_name})

    def move(self, file_id: str, new_parent_id: str | None) -> dict:
        meta = self.get_file(file_id)
        old_parents = meta.get("parents", [])
        body: dict = {}
        if new_parent_id:
            body["addParents"] = new_parent_id
        body["removeParents"] = ",".join(old_parents) if old_parents else None
        body = {k: v for k, v in body.items() if v is not None}
        return self._request("PATCH", f"{DRIVE_BASE}/files/{file_id}",
                             params={"fields": "id,parents,modifiedTime"},
                             json_body=body)

    def set_trashed(self, file_id: str, trashed: bool) -> dict:
        return self._request("PATCH", f"{DRIVE_BASE}/files/{file_id}",
                             params={"fields": "id,trashed,modifiedTime"},
                             json_body={"trashed": trashed})

    # -- change feed (incremental sync) -------------------------------------

    def get_start_page_token(self) -> str:
        data = self._request("GET", f"{DRIVE_BASE}/changes/startPageToken",
                             params={"supportsAllDrives": "true"})
        return data.get("startPageToken", "")

    def list_changes(self, page_token: str, page_size: int = 1000) -> dict:
        return self._request(
            "GET",
            f"{DRIVE_BASE}/changes",
            params={
                "pageToken": page_token,
                "pageSize": str(page_size),
                "includeRemoved": "true",
                "fields": CHANGES_FIELDS,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )

    # -- push notifications / watch channels (optional) ---------------------

    def watch(self, folder_id: str, channel_id: str, address: str,
              expiration_ms: int | None = None) -> dict:
        body: dict = {
            "id": channel_id,
            "type": "web_hook",
            "address": address,
            "resource": {"id": folder_id, "kind": "drive#file"},
        }
        if expiration_ms:
            body["expiration"] = str(expiration_ms)
        params = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
                  "fields": "id,resourceId,resourceUri,expiration,kind"}
        return self._request("POST", f"{DRIVE_BASE}/changes/watch", params=params, json_body=body)

    def stop_watch(self, channel_id: str, resource_id: str) -> None:
        self._request("POST", f"{DRIVE_BASE}/channels/stop",
                      json_body={"id": channel_id, "resourceId": resource_id})