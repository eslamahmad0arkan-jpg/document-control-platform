"""Per-user settings (theme) + API settings."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import SettingsUpdate
from ..services.audit_service import record_audit

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(user: User = Depends(get_current_user)):
    return {"theme": user.theme}


@router.patch("")
def update_settings(payload: SettingsUpdate,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if payload.theme:
        user.theme = payload.theme
        record_audit(db, user=user, action="SETTINGS_CHANGED",
                     details={"theme": payload.theme})
        db.commit()
    return {"theme": user.theme}