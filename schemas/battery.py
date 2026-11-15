"""Pydantic schemas: battery."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

from constants.enums import BatteryState, Chemistry, HealthStatus
from schemas.base import ORM

class BatteryCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=40, examples=["VOLT-0199"])
    chemistry: Chemistry = Chemistry.LFP
    rated_capacity_ah: float = Field(20.0, gt=0, le=500)
    nominal_voltage: float = Field(48.0, gt=0, le=120)
    station_id: Optional[str] = None
    locker_id: Optional[str] = None
    state: BatteryState = BatteryState.CHARGED


class BatteryStateUpdate(BaseModel):
    state: BatteryState
    station_id: Optional[str] = None
    locker_id: Optional[str] = None


class BatteryOut(BaseModel):
    model_config = ORM
    id: str
    chemistry: Chemistry
    rated_capacity_ah: float
    nominal_voltage: float
    full_charge_capacity_ah: float
    remaining_ah: float
    voltage: float
    current_a: float
    temperature_c: float
    soc: float
    soh: float
    cycle_count: int
    fault_count: int
    operating_hours: float
    supply_voltage_v: Optional[float] = None
    health_score: float
    health_status: HealthStatus
    state: BatteryState
    station_id: Optional[str] = None
    locker_id: Optional[str] = None
    van_id: Optional[int] = None
    last_seen_at: Optional[datetime] = None
