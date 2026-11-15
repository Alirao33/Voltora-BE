"""Pydantic schemas: booking."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field, model_validator
from typing import Optional

from constants.enums import ActionType, BookingStatus
from schemas.base import ORM

class BookLockerRequest(BaseModel):
    site_id: str
    locker_id: Optional[str] = None
    action_type: ActionType = ActionType.SWAP_DROP_AND_PICK
    scheduled_time: Optional[datetime] = None

    @model_validator(mode="after")
    def _no_guest_here(self):
        if self.action_type == ActionType.GUEST_PAY_SWAP:
            raise ValueError("Guest swaps are created via /api/guest/pay-and-swap")
        return self


class SmartSwapRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    max_distance_km: float = Field(25.0, gt=0, le=100)
    auto_book: bool = True
    scheduled_time: Optional[datetime] = None


class BookingActionRequest(BaseModel):
    unlock_code: Optional[str] = None


class BookingOut(BaseModel):
    model_config = ORM
    id: int
    booking_id: str
    user_id: Optional[int] = None
    station_id: str
    locker_id: str
    action_type: ActionType
    scheduled_time: datetime
    hold_expires_at: datetime
    unlock_code: str
    status: BookingStatus
