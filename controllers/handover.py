"""Thin controllers (HTTP in, module call, HTTP out): rider <-> delivery man handover."""

from __future__ import annotations

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import handover as handover_service


class MessageIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=500)


class AllowIn(BaseModel):
    allow: bool


def list_messages(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return handover_service.list_messages(order_id=order_id, user=user, db=db)


def send_message(order_id: int, body: MessageIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return handover_service.send_message(order_id=order_id, body=body.body, user=user, db=db)


def request_locker_code(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return handover_service.request_locker_code(order_id=order_id, user=user, db=db)


def respond_locker_code(order_id: int, body: AllowIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return handover_service.respond_locker_code(order_id=order_id, allow=body.allow, user=user, db=db)
