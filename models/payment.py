"""ORM model: payment."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base, enum_col
from constants.enums import PaymentMethod, PaymentStatus
from utils.dates import now_utc

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), index=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(10), default="ORDER")     # ORDER | PLAN
    method: Mapped[PaymentMethod] = mapped_column(enum_col(PaymentMethod))
    reference: Mapped[str] = mapped_column(String(100), unique=True)
    amount_pkr: Mapped[float] = mapped_column(Float)
    refunded_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[PaymentStatus] = mapped_column(enum_col(PaymentStatus), default=PaymentStatus.PENDING)
    refund_reason: Mapped[Optional[str]] = mapped_column(String(200))
    provider_ref: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
