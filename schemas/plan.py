"""Pydantic schemas: plan."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

from constants.enums import PaymentMethod, PlanKind, SubscriptionStatus
from schemas.base import ORM

class PlanCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=30)
    name: str = Field(..., min_length=1, max_length=80)
    kind: PlanKind
    swaps_included: int = Field(0, ge=0, le=1000)
    price_pkr: float = Field(0.0, ge=0)
    duration_type: str = Field("monthly", max_length=20)
    duration_days: int = Field(30, ge=1, le=366)


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    swaps_included: Optional[int] = Field(None, ge=0, le=1000)
    price_pkr: Optional[float] = Field(None, ge=0)
    duration_days: Optional[int] = Field(None, ge=1, le=366)
    is_active: Optional[bool] = None


class PlanOut(BaseModel):
    model_config = ORM
    id: int
    code: str
    name: str
    kind: PlanKind
    swaps_included: int
    price_pkr: float
    duration_type: str
    duration_days: int
    is_active: bool


class SubscriptionOut(BaseModel):
    model_config = ORM
    id: int
    plan_id: int
    swaps_remaining: int
    starts_at: datetime
    ends_at: datetime
    status: SubscriptionStatus


class SubscribeRequest(BaseModel):
    payment_method: PaymentMethod
    payment_reference: str = Field(..., min_length=4, max_length=100)


class SubscriptionRequest(BaseModel):
    plan_code: str
