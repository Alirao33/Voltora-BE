"""Thin controllers (HTTP in, module call, HTTP out): audit."""

from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import audit as audit_service

def list_audit(limit: int = Query(100, ge=1, le=500), actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return audit_service.list_audit(limit=limit, actor=actor, db=db)
