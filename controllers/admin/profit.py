"""Thin controllers (HTTP in, module call, HTTP out): profit log, platform settings and fees."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import profit as profit_service
from modules.profit import PlatformSettings
from modules.reports import _naive_utc


def profit_log(start: Optional[datetime] = None, end: Optional[datetime] = None, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return profit_service.profit_log(actor=actor, db=db, start=_naive_utc(start), end=_naive_utc(end))


def get_platform_settings(db: Session = Depends(get_db)):
    return profit_service.get_platform_settings(db)


def set_platform_settings(body: PlatformSettings, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return profit_service.set_platform_settings(body=body, actor=actor, db=db)


def list_fees(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return profit_service.list_fees(actor=actor, db=db)


def mark_fee_received(fee_id: int, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return profit_service.mark_fee_received(fee_id=fee_id, actor=actor, db=db)
