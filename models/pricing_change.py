"""ORM model: a station admin's request to change the pricing rates, waiting for the super admin."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base
from utils.dates import now_utc


class PricingChange(Base):
    __tablename__ = "pricing_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    requested_by_name: Mapped[str] = mapped_column(String(120))
    station_id: Mapped[Optional[str]] = mapped_column(String(40))
    proposed: Mapped[dict] = mapped_column(JSON)  # the full pricing config being asked for
    current: Mapped[dict] = mapped_column(JSON)  # the live config when it was asked, so the diff stays readable
    status: Mapped[str] = mapped_column(String(12), default="PENDING", index=True)  # PENDING APPROVED REJECTED CANCELLED SUPERSEDED
    decided_by_name: Mapped[Optional[str]] = mapped_column(String(120))
    decided_at: Mapped[Optional[dt.datetime]]
    decision_note: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
