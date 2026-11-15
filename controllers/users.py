"""Thin controllers (HTTP in, module call, HTTP out): users."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import users as users_service
from schemas.auth import UserAdminUpdate

def me(user: User = Depends(get_current_user)):
    return users_service.me(user=user)


def list_users(db: Session = Depends(get_db)):
    return users_service.list_users(db=db)


def update_user(user_id: int, body: UserAdminUpdate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return users_service.update_user(user_id=user_id, body=body, actor=actor, db=db)
