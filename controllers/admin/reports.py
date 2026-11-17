"""Thin controllers (HTTP in, module call, HTTP out): reports."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import reports as reports_service

def report_summary(start: Optional[datetime] = None, end: Optional[datetime] = None, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports_service.report_summary(actor=actor, db=db, start=start, end=end)


def report_log(start: Optional[datetime] = None, end: Optional[datetime] = None, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports_service.report_log(actor=actor, db=db, start=start, end=end)


def report_csv(start: Optional[datetime] = None, end: Optional[datetime] = None, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports_service.report_csv(actor=actor, db=db, start=start, end=end)


def report_revenue(days: int = Query(30, ge=1, le=365), format: str = Query("json", pattern="^(json|csv)$"), actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports_service.report_revenue(days=days, format=format, actor=actor, db=db)


def report_station_kpi(days: int = Query(7, ge=1, le=90), actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports_service.report_station_kpi(days=days, actor=actor, db=db)


def get_analytics_overview(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports_service.get_analytics_overview(actor=actor, db=db)


def get_finance_summary(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports_service.get_finance_summary(actor=actor, db=db)
