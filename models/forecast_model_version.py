"""ORM model: forecast model version."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Boolean, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base
from utils.dates import now_utc

class ForecastModelVersion(Base):
    __tablename__ = "forecast_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(40))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_path: Mapped[str] = mapped_column(String(300))
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    trained_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
