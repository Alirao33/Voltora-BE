"""Thin controllers (HTTP in, module call, HTTP out): lockers."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import lockers as lockers_service
from schemas.locker import LockerCreate, LockerUpdate

def create_locker(body: LockerCreate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return lockers_service.create_locker(body=body, actor=actor, db=db)


def update_locker(locker_id: str, body: LockerUpdate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return lockers_service.update_locker(locker_id=locker_id, body=body, actor=actor, db=db)
