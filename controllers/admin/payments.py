"""Thin controllers (HTTP in, module call, HTTP out): payments."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session
from typing import Optional

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import payments as payments_service


def list_payments(status: Optional[str] = None, limit: int = 100, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return payments_service.list_payments(actor=actor, db=db, status=status, limit=limit)
