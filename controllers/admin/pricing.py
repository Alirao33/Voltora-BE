"""Thin controllers (HTTP in, module call, HTTP out): pricing."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import lockers as lockers_service
from modules import pricing as pricing_service
from modules import pricing_approval as approval_service
from modules.pricing import PricingConfig
from schemas.locker import LockerRateUpdateRequest

def get_pricing(db: Session = Depends(get_db)):
    return pricing_service.get_pricing(db=db)


def set_pricing(body: PricingConfig, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return approval_service.submit_pricing(body=body, actor=actor, db=db)


def list_pricing_changes(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return approval_service.list_changes(actor=actor, db=db)


class Decision(BaseModel):
    approve: bool
    note: Optional[str] = None


def decide_pricing_change(change_id: int, body: Decision, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return approval_service.decide_change(change_id=change_id, approve=body.approve, note=body.note, actor=actor, db=db)


def cancel_pricing_change(change_id: int, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return approval_service.cancel_change(change_id=change_id, actor=actor, db=db)


def update_locker_rates(req: LockerRateUpdateRequest, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Dynamically recalculate Weekly, Monthly, and Yearly locker pass rates from a base daily rate."""
    return lockers_service.update_locker_rates(req=req, actor=actor, db=db)
