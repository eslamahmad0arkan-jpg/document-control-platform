"""Report aggregation service. Reads from the synced metadata + activity tables."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Activity, File, Folder

MAX_BUCKETS = 100


def _dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return None


def build_reports(db: Session, project_id: int,
                  date_from: datetime | None = None,
                  date_to: datetime | None = None) -> dict:
    q_act = db.query(Activity).filter(Activity.project_id == project_id)
    if date_from:
        q_act = q_act.filter(Activity.detected_at >= date_from)
    if date_to:
        q_act = q_act.filter(Activity.detected_at < date_to)

    act_q = q_act

    # ---- overview ---------------------------------------------------------
    total_files = db.query(func.count(File.id)).filter(
        File.project_id == project_id, File.trashed.is_(False)
    ).scalar() or 0
    total_folders = db.query(func.count(Folder.id)).filter(
        Folder.project_id == project_id, Folder.trashed.is_(False)
    ).scalar() or 0
    total_size = db.query(func.coalesce(func.sum(File.size), 0)).filter(
        File.project_id == project_id, File.trashed.is_(False)
    ).scalar() or 0
    new_files = act_q.filter(Activity.action == "CREATED",
                             Activity.target_type == "FILE").count()
    modified_files = act_q.filter(Activity.action == "MODIFIED",
                                  Activity.target_type == "FILE").count()
    trashed = act_q.filter(Activity.action == "TRASHED").count()
    total_activities = act_q.count()

    active_users_sub = (
        db.query(Activity.actor_email, Activity.user_id)
        .filter(Activity.project_id == project_id)
    )
    if date_from:
        active_users_sub = active_users_sub.filter(Activity.detected_at >= date_from)
    if date_to:
        active_users_sub = active_users_sub.filter(Activity.detected_at < date_to)
    distinct_actors = set()
    for email, uid in active_users_sub.all():
        if uid:
            distinct_actors.add(f"u{uid}")
        elif email:
            distinct_actors.add(email)
    active_users = len(distinct_actors)

    overview = {
        "total_files": total_files,
        "total_folders": total_folders,
        "new_files": new_files,
        "modified_files": modified_files,
        "trashed_files": trashed,
        "total_activities": total_activities,
        "active_users": active_users,
        "total_size": total_size,
    }

    # ---- activity breakdowns ---------------------------------------------
    by_day = _bucket(
        db, act_q, Activity.detected_at, _day_key, "date",
        date_from=date_from, date_to=date_to,
    )
    by_action = _bucket(db, act_q, Activity.action, lambda r: r, "action")
    by_user_raw = _bucket(db, act_q, Activity.actor_name, lambda r: r or "Unknown", "user")
    by_user = _merge_low(by_user_raw, 15, "user")
    by_folder_raw = _bucket(
        db, act_q.filter(Activity.folder_path.isnot(None)),
        Activity.folder_path, lambda r: r or "Project Root", "folder",
    )
    by_folder = _merge_low(by_folder_raw, 15, "folder")

    # ---- file breakdowns --------------------------------------------------
    by_ext_raw = _bucket(
        db, db.query(File).filter(File.project_id == project_id,
                                  File.trashed.is_(False)),
        File.extension, lambda r: (r or "other").upper(), "extension",
    )
    by_ext = _merge_low(by_ext_raw, 15, "extension")
    by_folder_files_raw = _bucket(
        db, db.query(File).filter(File.project_id == project_id,
                                  File.trashed.is_(False)),
        File.path, _folder_of, "folder",
    )
    by_folder_files = _merge_low(by_folder_files_raw, 15, "folder")

    recent_files = (
        db.query(File)
        .filter(File.project_id == project_id, File.trashed.is_(False))
        .order_by(File.modified_time.desc().nullslast())
        .limit(15)
        .all()
    )

    return {
        "overview": overview,
        "activity": {
            "by_day": by_day,
            "by_user": by_user,
            "by_action": by_action,
            "by_folder": by_folder,
        },
        "files": {
            "by_extension": by_ext,
            "by_folder": by_folder_files,
            "recent_files": [
                {
                    "id": f.id, "drive_id": f.drive_id, "type": "FILE",
                    "name": f.name, "mime_type": f.mime_type, "extension": f.extension,
                    "size": f.size, "path": f.path, "drive_url": f.drive_url,
                    "modified_time": _dt(f.modified_time),
                    "modified_by_name": f.modified_by_name,
                }
                for f in recent_files
            ],
        },
    }


def _bucket(db: Session, query, column, key_fn, label: str, *,
            date_from=None, date_to=None) -> list[dict]:
    rows = query.with_entities(column, func.count()).group_by(column).order_by(
        func.count().desc()
    ).limit(MAX_BUCKETS).all()
    if not rows:
        return []
    grouped: dict[str, int] = {}
    ordered: list[str] = []
    for raw, count in rows:
        key = key_fn(raw)
        if key not in grouped:
            grouped[key] = 0
            ordered.append(key)
        grouped[key] += count
    items = []
    for key in ordered:
        items.append({"key": str(key), "label": key, "count": grouped[key], "extra": {}})
    # chronological ordering for the day series
    try:
        items.sort(key=lambda i: i["key"])
    except Exception:
        pass
    return items


def _day_key(value) -> str:
    dt = _dt(value)
    return dt.strftime("%Y-%m-%d") if dt else "unknown"


def _folder_of(path) -> str:
    if not path:
        return "Project Root"
    parts = path.split("/")
    return "/".join(parts[:-1]) or "Project Root"


def _merge_low(items: list[dict], cap: int, label: str) -> list[dict]:
    merged = items[:cap]
    # preserve natural order for time series
    return merged