"""ORM model: station."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base
from utils.dates import now_utc

class Station(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    address: Mapped[str] = mapped_column(String(300))
    total_lockers: Mapped[int] = mapped_column(Integer, default=0)
    host_name: Mapped[Optional[str]] = mapped_column(String(120))          # shop / fuel station / mall
    host_revenue_share_pct: Mapped[float] = mapped_column(Float, default=0.0)
    is_depot: Mapped[bool] = mapped_column(Boolean, default=False)         # van loading point, not public
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    target_stock: Mapped[int] = mapped_column(Integer, default=0)          # S_target (set by an admin)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
