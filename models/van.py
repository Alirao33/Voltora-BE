"""ORM model: van."""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base

class Van(Base):
    __tablename__ = "vans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    driver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    capacity: Mapped[int] = mapped_column(Integer, default=20)
    depot_station_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stations.id"))
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
