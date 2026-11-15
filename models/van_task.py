"""ORM model: van task."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base, enum_col
from constants.enums import TaskKind, TaskStatus
from utils.dates import now_utc

class VanTask(Base):
    __tablename__ = "van_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    van_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vans.id"), index=True)
    kind: Mapped[TaskKind] = mapped_column(enum_col(TaskKind))
    status: Mapped[TaskStatus] = mapped_column(enum_col(TaskStatus), default=TaskStatus.PENDING, index=True)
    plan_date: Mapped[dt.datetime] = mapped_column(default=now_utc)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    station_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stations.id"))
    order_id: Mapped[Optional[int]] = mapped_column(Integer)
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    address: Mapped[Optional[str]] = mapped_column(String(300))
    drop_count: Mapped[int] = mapped_column(Integer, default=0)
    pickup_count: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    confirmed_drop: Mapped[Optional[int]] = mapped_column(Integer)
    confirmed_pickup: Mapped[Optional[int]] = mapped_column(Integer)
    confirmed_at: Mapped[Optional[dt.datetime]]

    def _customer(self):
        from sqlalchemy.orm import object_session
        from models.order import Order
        from models.user import User

        db = object_session(self)
        order = db.get(Order, self.order_id) if db is not None and self.order_id else None
        return db.get(User, order.user_id) if order and order.user_id else None

    @property
    def order_ref(self) -> Optional[str]:
        return f"ORD-{self.order_id}" if self.order_id else None

    @property
    def customer_name(self) -> Optional[str]:
        u = self._customer()
        return (u.full_name or u.username) if u else None

    @property
    def customer_phone(self) -> Optional[str]:
        u = self._customer()
        return u.phone if u else None
