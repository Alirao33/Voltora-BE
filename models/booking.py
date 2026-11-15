"""ORM model: booking."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base, enum_col
from constants.enums import ActionType, BookingStatus
from utils.dates import now_utc

class Booking(Base):
    """A held locker slot with an unlock code."""
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    guest_ref: Mapped[Optional[str]] = mapped_column(String(40))
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"))
    locker_id: Mapped[str] = mapped_column(ForeignKey("lockers.id"))
    action_type: Mapped[ActionType] = mapped_column(enum_col(ActionType))
    scheduled_time: Mapped[dt.datetime]
    hold_expires_at: Mapped[dt.datetime]
    unlock_code: Mapped[str] = mapped_column(String(8))
    status: Mapped[BookingStatus] = mapped_column(enum_col(BookingStatus), default=BookingStatus.PENDING)
    order_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)

    @property
    def booking_id(self) -> str:
        return f"BK-{1000 + self.id}"
