"""Pydantic schemas: order."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field, model_validator
from typing import Optional

from constants.enums import OrderMode, OrderStatus, PaymentMethod
from schemas.base import ORM

class OrderCreate(BaseModel):
    station_id: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    mode: OrderMode = OrderMode.STATION
    use_subscription: bool = True
    pay_later: bool = Field(False, description="Subscribed riders only: reserve now, pay the bill later.")
    capacity_used_ah: Optional[float] = Field(
        None, gt=0, le=200, description="Defaults to 80 % of the issued pack's capacity."
    )
    payment_method: Optional[PaymentMethod] = None
    payment_reference: Optional[str] = Field(None, min_length=4, max_length=100)
    scheduled_time: Optional[datetime] = None
    locker_id: Optional[str] = None
    delivery_address: Optional[str] = Field(None, max_length=300)
    delivery_lat: Optional[float] = Field(None, ge=-90, le=90)
    delivery_lon: Optional[float] = Field(None, ge=-180, le=180)

    @model_validator(mode="after")
    def _checks(self):
        if not self.station_id and (self.latitude is None or self.longitude is None):
            raise ValueError("Provide station_id, or both latitude and longitude")
        if self.mode == OrderMode.HOME and not (
            self.delivery_address and self.delivery_lat is not None and self.delivery_lon is not None
        ):
            raise ValueError("Home delivery needs delivery_address, delivery_lat and delivery_lon")
        return self


class OrderOut(BaseModel):
    model_config = ORM
    id: int
    order_id: str
    station_id: str
    mode: OrderMode
    status: OrderStatus
    slot_id: Optional[str] = None
    pickup_code: Optional[str] = None
    pack_out_id: Optional[str] = None
    pack_in_id: Optional[str] = None
    price: dict
    total_pkr: float
    charged_pkr: float
    actual_total_pkr: Optional[float] = None
    settlement_delta_pkr: Optional[float] = None
    delivery_address: Optional[str] = None
    delivery_otp: Optional[str] = None
    rating: Optional[int] = None
    receipt_no: Optional[str] = None
    hold_expires_at: Optional[datetime] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    payment_due_at: Optional[datetime] = None
    bill_paid_at: Optional[datetime] = None
    bill_penalty_pkr: float = 0.0
    bill_due_pkr: Optional[float] = None
    van_name: Optional[str] = None      # home delivery: set once the admin approves and a van is assigned
    driver_name: Optional[str] = None


class PayBillRequest(BaseModel):
    payment_method: PaymentMethod
    payment_reference: str = Field(..., min_length=4, max_length=100)


class UnlockRequest(BaseModel):
    pickup_code: str = Field(..., min_length=3, max_length=8)


class CompleteOrderRequest(BaseModel):
    returned_pack_id: Optional[str] = Field(None, description="Depleted pack handed back (QR scan).")


class RateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    review: Optional[str] = Field(None, max_length=500)


class OrderReviewRequest(BaseModel):
    action: str
    reason: Optional[str] = None
