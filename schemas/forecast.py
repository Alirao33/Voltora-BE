"""Pydantic schemas: forecast."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

class WeatherIn(BaseModel):
    ts: Optional[datetime] = None
    temp_c: float = Field(..., ge=-20, le=60)
    rain_mm: float = Field(0.0, ge=0, le=500)


class TrainRequest(BaseModel):
    prefer_xgboost: bool = True


class PilotRequest(BaseModel):
    n_stations: int = Field(6, ge=2, le=30)
    days: int = Field(7, ge=1, le=60)
    seed: int = 42
    van_capacity: int = Field(20, ge=1, le=200)
