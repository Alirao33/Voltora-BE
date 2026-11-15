"""Pydantic schemas: payment."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

from constants.enums import PaymentMethod, PaymentStatus
from schemas.base import ORM

class PaymentOut(BaseModel):
    model_config = ORM
    id: int
    order_id: Optional[int] = None
    purpose: str
    method: PaymentMethod
    reference: str
    amount_pkr: float
    refunded_pkr: float
    status: PaymentStatus
    refund_reason: Optional[str] = None
    created_at: datetime


class RefundRequest(BaseModel):
    amount_pkr: Optional[float] = Field(None, gt=0)
    reason: str = Field("admin refund", max_length=200)


class PaymentWebhook(BaseModel):
    reference: str
    status: str                       # SUCCEEDED | FAILED
    provider_ref: Optional[str] = None
