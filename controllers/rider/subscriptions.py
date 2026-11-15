"""Thin controllers (HTTP in, module call, HTTP out): subscriptions."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import subscriptions as subscriptions_service
from schemas.plan import SubscribeRequest

def my_subscription(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return subscriptions_service.my_subscription(user=user, db=db)


def cancel_subscription(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return subscriptions_service.cancel_subscription(user=user, db=db)


def subscribe_plan(plan_id: int, body: SubscribeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return subscriptions_service.subscribe_plan(plan_id=plan_id, body=body, user=user, db=db)
