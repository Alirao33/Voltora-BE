"""Thin controllers (HTTP in, module call, HTTP out): alerts."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import alerts as alerts_service

def list_alerts(open_only: bool = True, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return alerts_service.list_alerts(open_only=open_only, actor=actor, db=db)


def ack_alert(alert_id: int, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return alerts_service.ack_alert(alert_id=alert_id, actor=actor, db=db)


def resolve_alert(alert_id: int, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return alerts_service.resolve_alert(alert_id=alert_id, actor=actor, db=db)
