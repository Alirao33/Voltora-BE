"""ORM model: alert."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base, enum_col
from constants.enums import AlertSeverity, AlertType
from utils.dates import now_utc

class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[AlertType] = mapped_column(enum_col(AlertType), index=True)
    severity: Mapped[AlertSeverity] = mapped_column(enum_col(AlertSeverity))
    message: Mapped[str] = mapped_column(String(300))
    station_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    pack_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
