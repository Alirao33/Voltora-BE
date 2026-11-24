"""Thin controllers (HTTP in, module call, HTTP out): batteries."""

from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from config.database import get_db
from constants.enums import BatteryState
from guards.auth import get_current_user
from models import User
from modules import batteries as batteries_service
from schemas.battery import BatteryCreate, BatteryStateUpdate

def list_batteries(station_id: Optional[str] = None, state: Optional[BatteryState] = None, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return batteries_service.list_batteries(station_id=station_id, state=state, actor=actor, db=db)


def create_battery(body: BatteryCreate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return batteries_service.create_battery(body=body, actor=actor, db=db)


def update_battery(pack_id: str, body: BatteryStateUpdate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return batteries_service.update_battery(pack_id=pack_id, body=body, actor=actor, db=db)


def get_second_life_batteries(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return batteries_service.get_second_life_batteries(actor=actor, db=db)


def get_battery(pack_id: str, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return batteries_service.get_battery(pack_id=pack_id, actor=actor, db=db)


def telemetry_history(pack_id: str, hours: int = Query(24, ge=1, le=720), limit: int = Query(500, ge=1, le=5000), actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return batteries_service.telemetry_history(pack_id=pack_id, hours=hours, limit=limit, actor=actor, db=db)
