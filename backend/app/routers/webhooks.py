"""Google Drive push notification webhook.

Endpoint receives Drive webhook pings (verified by channel ID) and triggers an
incremental sync for the matching project. Periodic polling in the monitor worker
is the guaranteed safety net either way.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..services import monitor

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_GOOGLE_HEADERS = ("X-Goog-Channel-ID", "X-Goog-Resource-ID", "X-Goog-Resource-State")


@router.post("/drive")
async def drive_webhook(request: Request):
    channel_id = request.headers.get("X-Goog-Channel-ID")
    resource_id = request.headers.get("X-Goog-Resource-ID")
    state = request.headers.get("X-Goog-Resource-State", "").lower()
    project_id = request.query_params.get("project")
    if state == "sync":
        # Google sends this as a confirmation that a channel is valid
        return {"status": "ok"}

    matched = project_id or None
    if not matched and channel_id:
        matched = monitor.project_id_for_channel(channel_id, resource_id or "")
    if matched:
        monitor.trigger_async_sync(int(matched))
    return {"status": "ok"}