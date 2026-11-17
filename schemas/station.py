"""Pydantic schemas: station."""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional

class StationCreate(BaseModel):
    id: Optional[str] = Field(None, min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=120)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    address: str = Field(..., min_length=1, max_length=300)
    total_lockers: int = Field(10, ge=10, le=500)
    host_name: Optional[str] = Field(None, max_length=120)
    host_revenue_share_pct: float = Field(0.0, ge=0, le=100)
    is_depot: bool = False


class StationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    address: Optional[str] = Field(None, max_length=300)
    total_lockers: Optional[int] = Field(None, ge=10, le=500)
    host_name: Optional[str] = None
    host_revenue_share_pct: Optional[float] = Field(None, ge=0, le=100)
    target_stock: Optional[int] = Field(None, ge=0, le=1000)
    is_active: Optional[bool] = None


class StationCreateRequest(BaseModel):
    name: str
    city: str
    address: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    status: str = "OPERATIONAL"
    total_lockers: int = Field(10, ge=10, le=500)
