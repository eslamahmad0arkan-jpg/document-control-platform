"""Reports dashboard + CSV/Excel/PDF export."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_project_for_user
from ..errors import AppError
from ..models import User
from ..schemas import ReportsResponse
from ..services.export import export_csv, export_pdf, export_xlsx
from ..services.report_service import build_reports

router = APIRouter(prefix="/api/projects/{project_id}/reports", tags=["reports"])


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AppError(f"Invalid date: {value}")


@router.get("", response_model=ReportsResponse)
def reports(project_id: int, date_from: str | None = None, date_to: str | None = None,
            days: int | None = Query(None, ge=1, le=365),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    d_from = _parse(date_from)
    d_to = _parse(date_to)
    if days and not d_from:
        now = datetime.now(timezone.utc)
        d_from = now.replace(microsecond=0)
        from datetime import timedelta

        d_from = d_from - timedelta(days=days)
    result = build_reports(db, project_id, d_from, d_to)
    result["overview"]["date_from"] = d_from
    result["overview"]["date_to"] = d_to
    return result


@router.get("/export")
def export(project_id: int, format: str = "csv",
           date_from: str | None = None, date_to: str | None = None,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project, _m, _r = get_project_for_user(project_id, user, db)
    report = build_reports(db, project_id, _parse(date_from), _parse(date_to))
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in project.name)[:40]
    content_type = "text/csv"
    filename = f"{safe_name}-report.csv"
    if format == "xlsx":
        body = export_xlsx(report)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{safe_name}-report.xlsx"
    elif format == "pdf":
        body = export_pdf(report, project.name)
        content_type = "application/pdf"
        filename = f"{safe_name}-report.pdf"
    else:
        body = export_csv(report)
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=body, media_type=content_type, headers=headers)