"""Thin controllers (HTTP in, module call, HTTP out): subscriptions."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from modules import subscriptions as subscriptions_service
from schemas.plan import SubscriptionRequest

def get_plans(db: Session = Depends(get_db)):
    return subscriptions_service.list_locker_plans(db)


def create_subscription(req: SubscriptionRequest):
    return subscriptions_service.confirm_locker_pass(req)

