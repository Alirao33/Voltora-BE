"""ORM model: battery."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base, enum_col
from constants.enums import BatteryState, Chemistry, HealthStatus
from utils.dates import now_utc

class Battery(Base):
    __tablename__ = "batteries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)          # pack_id
    chemistry: Mapped[Chemistry] = mapped_column(enum_col(Chemistry), default=Chemistry.LFP)
    rated_capacity_ah: Mapped[float] = mapped_column(Float, default=20.0)
    nominal_voltage: Mapped[float] = mapped_column(Float, default=48.0)
    full_charge_capacity_ah: Mapped[float] = mapped_column(Float, default=20.0)
    remaining_ah: Mapped[float] = mapped_column(Float, default=20.0)
    voltage: Mapped[float] = mapped_column(Float, default=48.0)
    current_a: Mapped[float] = mapped_column(Float, default=0.0)
    temperature_c: Mapped[float] = mapped_column(Float, default=25.0)
    soc: Mapped[float] = mapped_column(Float, default=100.0)
    soh: Mapped[float] = mapped_column(Float, default=100.0)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    fault_count: Mapped[int] = mapped_column(Integer, default=0)
    operating_hours: Mapped[float] = mapped_column(Float, default=0.0)
    supply_voltage_v: Mapped[Optional[float]] = mapped_column(Float)
    health_score: Mapped[float] = mapped_column(Float, default=100.0)
    health_status: Mapped[HealthStatus] = mapped_column(enum_col(HealthStatus), default=HealthStatus.NORMAL)
    state: Mapped[BatteryState] = mapped_column(enum_col(BatteryState), default=BatteryState.CHARGED, index=True)
    station_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stations.id"), index=True)
    locker_id: Mapped[Optional[str]] = mapped_column(ForeignKey("lockers.id"))     # slot housing the pack
    van_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vans.id"))
    reserved_order_id: Mapped[Optional[int]] = mapped_column(Integer)
    last_seen_at: Mapped[Optional[dt.datetime]]
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
