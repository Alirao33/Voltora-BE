"""Thin controllers (HTTP in, module call, HTTP out): rider summary."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import rider_home


def rider_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return rider_home.rider_summary(user=user, db=db)
