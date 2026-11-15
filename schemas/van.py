"""Pydantic schemas: van."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

from constants.enums import TaskKind, TaskStatus
from schemas.base import ORM

class VanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    driver_id: Optional[int] = None
    capacity: int = Field(20, ge=1, le=200)
    depot_station_id: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)


class VanUpdate(BaseModel):
    driver_id: Optional[int] = None
    capacity: Optional[int] = Field(None, ge=1, le=200)
    is_active: Optional[bool] = None
    depot_station_id: Optional[str] = None


class VanOut(BaseModel):
    model_config = ORM
    id: int
    name: str
    driver_id: Optional[int] = None
    capacity: int
    depot_station_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: bool


class VanTaskOut(BaseModel):
    model_config = ORM
    id: int
    van_id: Optional[int] = None
    kind: TaskKind
    status: TaskStatus
    seq: int
    station_id: Optional[str] = None
    order_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    drop_count: int
    pickup_count: int
    priority: float
    distance_km: float
    confirmed_drop: Optional[int] = None
    confirmed_pickup: Optional[int] = None
    confirmed_at: Optional[datetime] = None
    order_ref: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None


class TaskConfirm(BaseModel):
    delivery_otp: Optional[str] = None
    confirmed_drop: Optional[int] = Field(None, ge=0)
    confirmed_pickup: Optional[int] = Field(None, ge=0)
    returned_pack_id: Optional[str] = None


class LocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class PlanRouteRequest(BaseModel):
    van_id: int
