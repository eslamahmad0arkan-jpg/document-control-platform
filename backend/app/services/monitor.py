"""Monitoring worker.

Design (honest about Google API reality):
- Primary: a periodic incremental sync loop that pulls the Drive change feed.
  No data is lost on failure because the change cursor is only advanced after a
  successful commit (see services/sync.py).
- Optional: Google Drive push notifications (watch channels) as a faster trigger.
  Drive webhook channels expire after ~1 hour, so the worker refreshes them and
  keeps periodic polling as a guaranteed fallback. Watch channels require a
  publicly reachable HTTPS endpoint (WEBHOOK_BASE_URL).
- Google Workspace Events API subscriptions require a Google Workspace account
  with admin/domain delegation, so it is intentionally NOT a hard dependency.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..database import SessionLocal
from ..models import Project, SyncState, WatchChannel, utcnow
from . import sync as sync_engine

logger = logging.getLogger("drivedoc.monitor")

_watch_project_lock = threading.Lock()
_tasks: dict[int, threading.Thread] = {}


def _pick_member(db, project):
    """Return any active project member (identity resolved later by sync engine)."""
    from ..models import ProjectMember, User

    member = (
        db.query(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .filter(ProjectMember.project_id == project.id, User.is_active.is_(True))
        .order_by(ProjectMember.created_at.asc())
        .first()
    )
    return member


def sync_project_now(project_id: int) -> None:
    """Safer cross-thread callable: builds its own DB session."""
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project or project.deleted_at is not None:
            return
        state = db.query(SyncState).filter(SyncState.project_id == project.id).first()
        has_snapshot = (
            db.query(SyncState.id).filter(SyncState.project_id == project.id).first()
            and _snapshot_exists(db, project.id)
        )
        user = _pick_member(db, project)
        if user is None:
            logger.info("[sync/%s] no active member with credentials; skipped", project.name)
            return
        if not has_snapshot:
            logger.info("[sync/%s] initial full scan", project.name)
            sync_engine.full_scan(db, project, user, record_activities=False)
            _ensure_watch(db, project)
        else:
            result = sync_engine.incremental_sync(db, project, user)
            _ensure_watch(db, project)
            if result.get("activities"):
                logger.info("[sync/%s] %d new activities", project.name, result["activities"])
    except Exception as exc:  # never let the worker die on one project
        logger.error("[sync/%s] failed: %s", project_id, exc)
    finally:
        db.close()


def _snapshot_exists(db, project_id: int) -> bool:
    from ..models import File

    return db.query(File.id).filter(File.project_id == project_id).first() is not None


def trigger_async_sync(project_id: int) -> None:
    """Start a one-off sync in a background thread (used by webhook/UI trigger)."""
    thread = threading.Thread(target=sync_project_now, args=(project_id,), daemon=True)
    _tasks[project_id] = thread
    thread.start()


# --------------------------------------------------------------------------
# Watch channels
# --------------------------------------------------------------------------

def _ensure_watch(db, project: Project) -> None:
    if not settings.WEBHOOK_BASE_URL:
        return
    with _watch_project_lock:
        channel = (
            db.query(WatchChannel)
            .filter(WatchChannel.project_id == project.id,
                    WatchChannel.state == "ACTIVE")
            .first()
        )
        now = utcnow()
        if channel and channel.expiration and channel.expiration > now + timedelta(minutes=10):
            return
        if channel:
            # expiry near: silently replace
            db.delete(channel)
            db.flush()
        try:
            user = _pick_member(db, project)
            if user is None:
                return
            _, drive = sync_engine.pick_sync_identity(db, project, user)
            channel_id = f"dcd-{project.id}-{uuid.uuid4().hex[:12]}"
            address = (
                f"{settings.WEBHOOK_BASE_URL.rstrip('/')}/api/webhooks/drive"
                f"?project={project.id}&token={uuid.uuid4().hex[:16]}"
            )
            expiration = now + timedelta(hours=1)
            data = drive.watch(project.google_folder_id, channel_id, address,
                               expiration_ms=int(expiration.timestamp() * 1000))
            db.add(WatchChannel(
                project_id=project.id,
                channel_id=channel_id,
                resource_id=data.get("resourceId"),
                resource_kind=data.get("kind"),
                address=address,
                state="ACTIVE",
                expiration=expiration,
                last_renewed_at=now,
            ))
            db.commit()
            logger.info("[watch/%s] channel %s registered", project.name, channel_id)
        except Exception as exc:  # watch is optional; polling still runs
            logger.warning("[watch/%s] could not register channel: %s", project.name, exc)
            db.rollback()


def project_id_for_channel(channel_id: str, resource_id: str) -> int | None:
    db = SessionLocal()
    try:
        row = (
            db.query(WatchChannel)
            .filter(WatchChannel.channel_id == channel_id)
            .first()
        )
        return row.project_id if row else None
    finally:
        db.close()


# --------------------------------------------------------------------------
# Worker thread
# --------------------------------------------------------------------------

class MonitorWorker(threading.Thread):
    def __init__(self, status_holder: dict):
        super().__init__(daemon=True)
        self._stopped = threading.Event()
        self._status = status_holder
        self._status["running"] = False
        self._status["last_loop_at"] = None

    def stop(self) -> None:
        self._status["running"] = False
        self._stopped.set()

    def run(self) -> None:
        logger.info("monitor worker started (interval=%ss)", settings.SYNC_INTERVAL_SECONDS)
        self._status["running"] = True
        while not self._stopped.is_set():
            self._status["last_loop_at"] = datetime.now(timezone.utc).isoformat()
            try:
                self._tick()
            except Exception as exc:
                logger.error("monitor tick failed: %s", exc)
            self._stopped.wait(settings.SYNC_INTERVAL_SECONDS)
        logger.info("monitor worker stopped")

    def _tick(self) -> None:
        db = SessionLocal()
        try:
            projects = (
                db.query(Project)
                .filter(Project.deleted_at.is_(None))
                .order_by(Project.id.asc())
                .all()
            )
        finally:
            db.close()
        for project in projects:
            if self._stopped.is_set():
                return
            _sync_with_backoff(project)
            time.sleep(0.2)


def _sync_with_backoff(project: Project) -> None:
    db = SessionLocal()
    try:
        state = db.query(SyncState).filter(SyncState.project_id == project.id).first()
        errors = state.consecutive_errors if state else 0
    finally:
        db.close()

    backoff = min(
        settings.SYNC_RETRY_BASE_SECONDS * (2 ** errors),
        settings.SYNC_MAX_BACKOFF_SECONDS,
    )
    if errors and backoff > 0:
        time.sleep(min(backoff, 60))

    sync_project_now(project.id)