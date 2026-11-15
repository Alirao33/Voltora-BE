"""Pydantic schemas: telemetry."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field, model_validator
from typing import Any, Optional

from schemas.base import ORM

class TelemetryIn(BaseModel):
    """One telemetry frame per slot. Supports aliases: current_a/current_amperage, temperature_c/temp_c."""
    pack_id: str = Field(..., min_length=1, max_length=40)
    ts: Optional[datetime] = None
    voltage: float = Field(..., ge=0, le=120)
    current_a: float = Field(0.0, ge=-300, le=300)
    temperature_c: float = Field(..., ge=-40, le=150)
    soc: Optional[float] = Field(None, ge=0, le=100)
    soh: Optional[float] = Field(None, ge=0, le=100)
    remaining_ah: Optional[float] = Field(None, ge=0, le=500)
    full_charge_ah: Optional[float] = Field(None, gt=0, le=500)
    rated_ah: Optional[float] = Field(None, gt=0, le=500)
    cycle_count: Optional[int] = Field(None, ge=0)
    fault_count: Optional[int] = Field(None, ge=0)
    operating_hours: Optional[float] = Field(None, ge=0)
    supply_voltage_v: Optional[float] = Field(None, ge=0, le=500)
    station_id: Optional[str] = None
    slot_id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data: Any):
        if isinstance(data, dict):
            data = dict(data)
            for old, new in (("current_amperage", "current_a"), ("temp_c", "temperature_c")):
                if old in data and new not in data:
                    data[new] = data.pop(old)
        return data


class TelemetryOut(BaseModel):
    model_config = ORM
    pack_id: str
    ts: datetime
    voltage: float
    current_a: float
    temperature_c: float
    soc: float
    soh: float
    cycle_count: int
    health_score: float
    supply_voltage_v: Optional[float] = None
