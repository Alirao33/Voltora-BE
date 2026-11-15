"""ORM model: weather observation."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base

class WeatherObservation(Base):
    __tablename__ = "weather_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(index=True)
    temp_c: Mapped[float] = mapped_column(Float)
    rain_mm: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(40), default="manual")
