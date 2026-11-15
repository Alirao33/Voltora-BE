"""ORM model: telemetry reading."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base
from utils.dates import now_utc

class TelemetryReading(Base):
    """Time-series table (hypertable on PostgreSQL+TimescaleDB). Composite PK deduplicates readings."""
    __tablename__ = "telemetry_readings"
    __table_args__ = (Index("ix_telemetry_ts", "ts"),)

    pack_id: Mapped[str] = mapped_column(ForeignKey("batteries.id"), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(primary_key=True)
    received_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
    station_id: Mapped[Optional[str]] = mapped_column(String(40))
    slot_id: Mapped[Optional[str]] = mapped_column(String(40))
    voltage: Mapped[float] = mapped_column(Float)
    current_a: Mapped[float] = mapped_column(Float, default=0.0)
    temperature_c: Mapped[float] = mapped_column(Float)
    soc: Mapped[float] = mapped_column(Float)
    soh: Mapped[float] = mapped_column(Float)
    remaining_ah: Mapped[Optional[float]] = mapped_column(Float)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    supply_voltage_v: Mapped[Optional[float]] = mapped_column(Float)
    health_score: Mapped[float] = mapped_column(Float, default=100.0)
