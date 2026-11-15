"""Pydantic schemas: swap."""

from __future__ import annotations

from pydantic import BaseModel, Field

class SwapRequest(BaseModel):
    rider_id: str
    station_id: str


class PhysicalSwapReturn(BaseModel):
    rider_id: str
    station_id: str
    returned_battery_id: str
    returned_soc: float = Field(..., ge=0, le=100)
    returned_soh: float = Field(..., ge=0, le=100)
