"""FastAPI application entrypoint.

Serves the REST API and the static SPA frontend from the same origin.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from .config import ROOT_DIR, settings
from .database import Base, engine
from .errors import AppError
from .services import monitor

logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("drivedoc")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("creating database tables (if missing)")
    Base.metadata.create_all(bind=engine)
    _apply_index_backfills()
    app.state.monitor_status = {"running": False, "last_loop_at": None}
    worker = None
    if settings.MONITOR_ENABLED:
        worker = monitor.MonitorWorker(app.state.monitor_status)
        worker.start()
    yield
    if worker:
        worker.stop()


def _apply_index_backfills() -> None:
    """SQLite create_all skips existing tables, so indexes added later are
    applied explicitly here. Postgres uses database/schema.sql instead."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_files_project_trashed "
            "ON files (project_id, trashed)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_folders_project_trashed "
            "ON folders (project_id, trashed)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_file_locks_active "
            "ON file_locks (drive_id, released_at)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_file_comments_file "
            "ON file_comments (project_id, drive_id, created_at)"
        )
        # Columns added to file_comments after the live SQLite table existed.
        # SQLite has no IF NOT EXISTS for columns → probe PRAGMA first.
        cols = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(file_comments)").fetchall()
        }
        if "parent_id" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE file_comments ADD COLUMN parent_id INTEGER "
                "REFERENCES file_comments(id) ON DELETE CASCADE"
            )
        if "mentions" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE file_comments ADD COLUMN mentions JSON DEFAULT '[]'"
            )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_file_comments_parent "
            "ON file_comments (parent_id)"
        )


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    body = exc.to_dict()
    if exc.status_code >= 500:
        logger.error("AppError %s", exc.message)
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "VALIDATION_ERROR",
                           "message": "Invalid request payload.",
                           "details": {"errors": exc.errors()}}},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR",
                           "message": "An unexpected error occurred.", "details": {}}},
    )


from .routers import (  # noqa: E402
    activities,
    admin,
    auth,
    docr,
    explorer,
    notifications,
    projects,
    reports,
    search,
    settings as settings_router,
    structure,
    webhooks,
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(explorer.router)
app.include_router(docr.router)
app.include_router(activities.router)
app.include_router(notifications.router)
app.include_router(reports.router)
app.include_router(search.router)
app.include_router(search.global_router)
app.include_router(admin.router)
app.include_router(settings_router.router)
app.include_router(structure.router)
app.include_router(webhooks.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": "1.0.0"}


# ---- static SPA -------------------------------------------------------------

_frontend_dir = ROOT_DIR / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")