"""ORM model: subscription."""

from __future__ import annotations

import datetime as dt
from typing import Optional
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base, enum_col
from constants.enums import SubscriptionStatus
from utils.dates import now_utc

class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    swaps_remaining: Mapped[int] = mapped_column(Integer, default=0)
    starts_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
    ends_at: Mapped[dt.datetime]
    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_col(SubscriptionStatus), default=SubscriptionStatus.ACTIVE
    )
    station_id: Mapped[Optional[str]] = mapped_column(String(40))  # set once, when the rider picks a station
    locker_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
