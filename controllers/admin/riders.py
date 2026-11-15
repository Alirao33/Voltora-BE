"""Thin controllers (HTTP in, module call, HTTP out): riders."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import users as users_service
from schemas.auth import RiderStatusUpdate

def update_rider_status(rider_id: int, req: RiderStatusUpdate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return users_service.update_rider_status(rider_id=rider_id, req=req, actor=actor, db=db)
