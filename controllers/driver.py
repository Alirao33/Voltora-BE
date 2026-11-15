"""Thin controllers (HTTP in, module call, HTTP out): driver."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import delivery as delivery_service
from modules import fleet as fleet_service
from schemas.delivery import DeliveryHandoverOTP
from schemas.van import LocationUpdate, TaskConfirm

def driver_van(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.driver_van(user=user, db=db)


def driver_tasks(include_done: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.driver_tasks(user=user, db=db, include_done=include_done)


def update_location(body: LocationUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.update_location(body=body, user=user, db=db)


def confirm_driver_task(task_id: int, body: TaskConfirm = TaskConfirm(), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.confirm_driver_task(task_id=task_id, body=body, user=user, db=db)


def driver_return_to_depot(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.driver_return_to_depot(user=user, db=db)


def complete_delivery_handover(handover: DeliveryHandoverOTP, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Verify rider delivery OTP at doorstep, record any COD collection, and finalize the swap order."""
    return delivery_service.complete_delivery_handover(handover=handover, user=user, db=db)
