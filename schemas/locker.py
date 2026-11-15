"""Pydantic schemas: locker."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional

from constants.enums import DoorLockState, LockerStatus
from schemas.base import ORM

class LockerCreate(BaseModel):
    id: Optional[str] = Field(None, min_length=1, max_length=40)
    site_id: str
    door_number: Optional[str] = Field(None, min_length=1, max_length=40)
    max_battery_capacity: int = Field(2, ge=1, le=2)
    current_occupied_batteries: int = Field(0, ge=0)
    status: LockerStatus = LockerStatus.AVAILABLE
    door_lock_state: DoorLockState = DoorLockState.LOCKED
    reserved_for_user_id: Optional[int] = None

    @model_validator(mode="after")
    def _checks(self):
        if self.current_occupied_batteries > self.max_battery_capacity:
            raise ValueError("current_occupied_batteries exceeds max_battery_capacity")
        if self.status == LockerStatus.RESERVED_VIP and not self.reserved_for_user_id:
            raise ValueError("reserved_for_user_id is required for RESERVED_VIP lockers")
        return self


class LockerUpdate(BaseModel):
    status: Optional[LockerStatus] = Field(
        None, description="Only AVAILABLE or MAINTENANCE can be set manually."
    )
    max_battery_capacity: Optional[int] = Field(None, ge=1, le=2)
    door_lock_state: Optional[DoorLockState] = None

    @field_validator("status")
    @classmethod
    def _admin_statuses(cls, v):
        if v is not None and v not in (LockerStatus.AVAILABLE, LockerStatus.MAINTENANCE):
            raise ValueError("status can only be set to AVAILABLE or MAINTENANCE; other states are automatic")
        return v


class LockerOut(BaseModel):
    model_config = ORM
    id: str
    station_id: str
    door_number: str
    max_battery_capacity: int
    current_occupied_batteries: int
    status: LockerStatus
    door_lock_state: DoorLockState
    has_vacancy: bool


class LockerRateUpdateRequest(BaseModel):
    base_daily_rate: float = Field(..., gt=0)
    discount_factor: float = Field(1.0, gt=0, le=2.0)
