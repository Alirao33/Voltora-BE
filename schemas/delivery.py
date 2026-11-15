"""Pydantic schemas: delivery."""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional

class AddressValidationRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    address_text: str


class BatteryOrderRequest(BaseModel):
    is_locker_customer: bool
    delivery_address: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    battery_model: Optional[str] = "Voltora-NMC-72V"
    payment_method: str
    delivery_time_slot: Optional[str] = "ASAP"
    special_instructions: Optional[str] = None


class DeliveryHandoverOTP(BaseModel):
    order_id: str
    otp_code: str
    cash_collected_amount: float = 0.0
    returned_pack_id: Optional[str] = None
