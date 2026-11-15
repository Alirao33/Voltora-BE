"""ORM model: forecast record."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base
from utils.dates import now_utc

class ForecastRecord(Base):
    __tablename__ = "forecast_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"), index=True)
    model_id: Mapped[Optional[int]] = mapped_column(ForeignKey("forecast_models.id"))
    generated_at: Mapped[dt.datetime] = mapped_column(default=now_utc, index=True)
    horizon_hours: Mapped[int] = mapped_column(Integer)
    predicted_demand: Mapped[float] = mapped_column(Float)
    target_stock: Mapped[int] = mapped_column(Integer)
