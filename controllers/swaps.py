"""Thin controllers (HTTP in, module call, HTTP out): swaps."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import swaps as swaps_service
from schemas.swap import PhysicalSwapReturn, SwapRequest

def request_battery_swap(req: SwapRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return swaps_service.request_battery_swap(req=req, user=user, db=db)


def complete_physical_swap(data: PhysicalSwapReturn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return swaps_service.complete_physical_swap(data=data, user=user, db=db)


def get_rider_swap_history(rider_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return swaps_service.get_rider_swap_history(rider_id=rider_id, user=user, db=db)
