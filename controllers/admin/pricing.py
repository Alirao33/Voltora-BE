"""Thin controllers (HTTP in, module call, HTTP out): pricing."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import lockers as lockers_service
from modules import pricing as pricing_service
from modules.pricing import PricingConfig
from schemas.locker import LockerRateUpdateRequest

def get_pricing(db: Session = Depends(get_db)):
    return pricing_service.get_pricing(db=db)


def set_pricing(body: PricingConfig, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return pricing_service.set_pricing(body=body, actor=actor, db=db)


def update_locker_rates(req: LockerRateUpdateRequest, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Dynamically recalculate Weekly, Monthly, and Yearly locker pass rates from a base daily rate."""
    return lockers_service.update_locker_rates(req=req, actor=actor, db=db)
