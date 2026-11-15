"""Pydantic schemas: guest."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional

from constants.enums import Chemistry, OrderMode, PaymentMethod
from schemas.base import clean_phone

class GuestQuoteRequest(BaseModel):
    site_id: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    pack_id: Optional[str] = Field(None, description="Returned (depleted) pack")
    voltage: Optional[float] = Field(None, gt=0, le=120)
    capacity_used_ah: float = Field(..., gt=0, le=200)
    chemistry: Chemistry = Chemistry.LFP
    temperature_c: Optional[float] = Field(None, ge=-40, le=150)
    delivery: bool = False
    is_subscribed: bool = False
    plan_code: Optional[str] = None

    @model_validator(mode="after")
    def _need_location(self):
        if not self.site_id and (self.latitude is None or self.longitude is None):
            raise ValueError("Provide site_id, or both latitude and longitude")
        return self


class GuestSwapRequest(GuestQuoteRequest):
    guest_name: Optional[str] = Field(None, max_length=80)
    phone: Optional[str] = None
    payment_method: PaymentMethod
    payment_reference: str = Field(..., min_length=4, max_length=100)
    amount_paid_pkr: Optional[float] = Field(None, ge=0)
    mode: OrderMode = OrderMode.STATION
    delivery_address: Optional[str] = None
    delivery_lat: Optional[float] = Field(None, ge=-90, le=90)
    delivery_lon: Optional[float] = Field(None, ge=-180, le=180)
    _p = field_validator("phone")(clean_phone)
