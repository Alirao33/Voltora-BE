"""Thin controllers (HTTP in, module call, HTTP out): delivery."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import delivery as delivery_service
from schemas.delivery import AddressValidationRequest, BatteryOrderRequest

def validate_delivery_address(req: AddressValidationRequest, db: Session = Depends(get_db)):
    return delivery_service.validate_delivery_address(req=req, db=db)


def get_battery_catalogue(db: Session = Depends(get_db)):
    return delivery_service.get_battery_catalogue(db=db)


def create_battery_delivery_order(order: BatteryOrderRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return delivery_service.create_battery_delivery_order(order=order, user=user, db=db)
