"""ORM models: the profit log and the platform fees paid to the bootstrap admin."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base
from utils.dates import now_utc


class ProfitEntry(Base):
    """One row per swap: what SoC and SoH earned, what they cost, and how the profit was split. Rates are frozen here."""

    __tablename__ = "profit_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    station_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    units_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    soc_revenue_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    soc_cost_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    soh_revenue_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    soh_cost_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    profit_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    bootstrap_pct: Mapped[float] = mapped_column(Float, default=10.0)
    bootstrap_share_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    station_share_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)


class PlatformFee(Base):
    """A fee the bootstrap admin collects, for now only the one-off registration fee of a station admin."""

    __tablename__ = "platform_fees"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="REGISTRATION")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user_name: Mapped[str] = mapped_column(String(120))
    station_id: Mapped[Optional[str]] = mapped_column(String(40))
    amount_pkr: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(10), default="DUE", index=True)  # DUE | RECEIVED
    received_at: Mapped[Optional[dt.datetime]]
    received_by_name: Mapped[Optional[str]] = mapped_column(String(120))
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
