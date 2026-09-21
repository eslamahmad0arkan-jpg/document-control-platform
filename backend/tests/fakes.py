"""In-memory fake Google Drive for tests. Mirrors the shape of Drive REST API v3
responses that the sync engine and routers consume. No network involved."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _t(offset_days: int = 0, offset_hours: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=offset_days, hours=offset_hours)
    return dt.isoformat().replace("+00:00", "Z")


def _node(fid: str, name: str, parent_id: str | None, *, mime: str = None,
          size: int = 0, modified_days: int = 1, shortcut_target: tuple[str, str] | None = None) -> dict:
    node = {
        "id": fid,
        "name": name,
        "mimeType": mime or ("application/vnd.google-apps.folder"
                             if not size and "." not in name else "file/plain"),
        "size": str(size) if size else None,
        "createdTime": _t(modified_days + 1),
        "modifiedTime": _t(modified_days),
        "trashed": False,
        "parents": ([parent_id] if parent_id else []),
        "webViewLink": f"https://drive.google.com/file/d/{fid}/view",
        "lastModifyingUser": None,
        "capabilities": {"canEdit": True, "canDelete": True, "canRename": True,
                          "canMoveItemWithinDrive": True, "canAddChildren": True,
                          "canUploadChildren": True, "canTrashChildren": True},
    }
    if shortcut_target:
        target_fid, target_mime = shortcut_target
        node["mimeType"] = SHORTCUT
        node["size"] = None
        node["shortcutDetails"] = {"targetId": target_fid, "targetMimeType": target_mime}
    return node


FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"


def build_standard_tree() -> dict[str, dict]:
    """Returns a storage map replicating:

    PROJECT ABC
    ├── Drawings/dwg-001.dwg
    ├── Technical/Civil, Technical/BOQ.xlsx
    ├── Reports
    └── Correspondence/RFI-025.pdf
    """
    storage: dict[str, dict] = {}
    root = _node("root-abc", "PROJECT ABC", None, mime="application/vnd.google-apps.folder")
    storage["root-abc"] = root
    drawings = _node("f-drawings", "Drawings", "root-abc", mime=FOLDER)
    storage["f-drawings"] = drawings
    storage["f-dwg001"] = _node("f-dwg001", "dwg-001.dwg", "f-drawings", size=51200, modified_days=3)
    technical = _node("f-technical", "Technical", "root-abc", mime=FOLDER)
    storage["f-technical"] = technical
    storage["f-civil"] = _node("f-civil", "Civil", "f-technical", mime=FOLDER)
    storage["f-boq"] = _node("f-boq", "BOQ.xlsx", "f-technical",
                             size=204800, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             modified_days=0)
    storage["f-reports"] = _node("f-reports", "Reports", "root-abc", mime=FOLDER)
    storage["f-corr"] = _node("f-corr", "Correspondence", "root-abc", mime=FOLDER)
    storage["f-rfi"] = _node("f-rfi", "RFI-025.pdf", "f-corr",
                             size=102400, mime="application/pdf", modified_days=0)
    return storage


class FakeDrive:
    """Test double implementing the DriveClient surface."""

    def __init__(self, storage: dict[str, dict] | None = None):
        self.storage = storage if storage is not None else build_standard_tree()
        self.pending_changes: list[dict] = []
        self._token_counter = 100

    # -- metadata ----------------------------------------------------------

    def get_file(self, file_id: str) -> dict:
        if file_id not in self.storage:
            from app.errors import GoogleNotFoundError

            raise GoogleNotFoundError(details={"id": file_id})
        return dict(self.storage[file_id])

    def get_media(self, file_id: str, export_mime: str | None = None) -> tuple[bytes, str]:
        if file_id not in self.storage:
            from app.errors import GoogleNotFoundError

            raise GoogleNotFoundError(details={"id": file_id})
        node = self.storage[file_id]
        if export_mime:
            return b"%PDF-1.4 fake-export", export_mime
        name = node.get("name", "file")
        content_type = node.get("mimeType") or "application/octet-stream"
        return f"content-of:{name}".encode(), content_type

    def list_children(self, folder_id: str, *, trashed: bool = True,
                      order_by: str | None = None) -> list[dict]:
        out = [
            dict(n) for n in self.storage.values()
            if (n.get("parents") or [None])[0] == folder_id
            and (trashed or not n.get("trashed"))
        ]
        if order_by:
            out.sort(key=lambda n: n.get(order_by) or "")
        return out

    # -- mutations ----------------------------------------------------------

    def create_folder(self, parent_id: str | None, name: str) -> dict:
        fid = f"new-{len(self.storage) + 1}"
        node = _node(fid, name, parent_id, mime=FOLDER)
        self.storage[fid] = node
        self.pending_changes.append({"fileId": fid, "removed": False})
        return dict(node)

    def upload_file(self, parent_id: str, name: str, content: bytes, mime: str) -> dict:
        fid = f"up-{len(self.storage) + 1}"
        node = _node(fid, name, parent_id, size=len(content), mime=mime)
        self.storage[fid] = node
        self.pending_changes.append({"fileId": fid, "removed": False})
        return dict(node)

    def rename(self, file_id: str, new_name: str) -> dict:
        self._require(file_id)
        self.storage[file_id]["name"] = new_name
        self.storage[file_id]["modifiedTime"] = _t()
        self.pending_changes.append({"fileId": file_id, "removed": False})
        return {"id": file_id, "name": new_name, "modifiedTime": self.storage[file_id]["modifiedTime"]}

    def move(self, file_id: str, new_parent_id: str | None) -> dict:
        self._require(file_id)
        self.storage[file_id]["parents"] = [new_parent_id] if new_parent_id else []
        self.storage[file_id]["modifiedTime"] = _t()
        self.pending_changes.append({"fileId": file_id, "removed": False})
        return {"id": file_id, "parents": self.storage[file_id]["parents"]}

    def set_trashed(self, file_id: str, trashed: bool) -> dict:
        self._require(file_id)
        self.storage[file_id]["trashed"] = trashed
        self.storage[file_id]["modifiedTime"] = _t()
        self.pending_changes.append({"fileId": file_id, "removed": trashed})
        return {"id": file_id, "trashed": trashed, "modifiedTime": self.storage[file_id]["modifiedTime"]}

    def _require(self, file_id: str) -> None:
        if file_id not in self.storage:
            from app.errors import GoogleNotFoundError

            raise GoogleNotFoundError(details={"id": file_id})

    # -- change feed --------------------------------------------------------

    def get_start_page_token(self) -> str:
        return str(self._token_counter)

    def list_changes(self, page_token: str, page_size: int = 1000) -> dict:
        changes = list(self.pending_changes)
        self.pending_changes.clear()
        self._token_counter += 1
        return {
            "changes": changes,
            "nextPageToken": None,
            "newStartPageToken": str(self._token_counter),
        }

    # helper: mutate storage and record the change atomically
    def apply_change(self, file_id: str, mutate=None) -> None:
        if mutate:
            mutate(self.storage[file_id])
        self.pending_changes.append({"fileId": file_id, "removed": False})

    def apply_removal(self, file_id: str) -> None:
        self.pending_changes.append({"fileId": file_id, "removed": True})

    # -- watch --------------------------------------------------------------

    def watch(self, folder_id: str, channel_id: str, address: str,
              expiration_ms: int | None = None) -> dict:
        return {"id": channel_id, "resourceId": "res-1", "kind": "drive#change",
                "expiration": str(expiration_ms or 0)}

    def stop_watch(self, channel_id: str, resource_id: str) -> None:
        pass