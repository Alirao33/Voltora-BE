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

    def _order(self):
        from sqlalchemy.orm import object_session
        from models.order import Order

        db = object_session(self)
        return db.get(Order, self.order_id) if db is not None and self.order_id else None

    @property
    def locker_code_state(self) -> str:
        o = self._order()
        return "NONE" if o is None else "SHARED" if o.locker_code_shared else "REQUESTED" if o.locker_code_requested else "NONE"

    @property
    def locker_code(self) -> Optional[str]:
        o = self._order()  # only after the rider allowed it
        return o.pickup_code if o is not None and o.locker_code_shared else None

    @property
    def order_total_pkr(self) -> Optional[float]:
        o = self._order()
        return (o.actual_total_pkr if o.actual_total_pkr is not None else o.total_pkr) if o else None

    @property
    def delivery_charge_pkr(self) -> Optional[float]:
        o = self._order()
        return (o.price or {}).get("delivery_distance_charge_pkr") if o else None

    @property
    def delivery_km(self) -> Optional[float]:
        o = self._order()
        if not o:
            return None
        km = (o.price or {}).get("delivery_km")
        if km is None and self.latitude is not None and o.delivery_lat is not None:
            from sqlalchemy.orm import object_session
            from models.station import Station
            from utils.geo import haversine_km

            st = object_session(self).get(Station, o.station_id)
            km = round(haversine_km(st.latitude, st.longitude, o.delivery_lat, o.delivery_lon), 2) if st else None
        return km

    @property
    def payment_note(self) -> Optional[str]:
        o = self._order()
        if not o:
            return None
        return ("Pay later · bill paid" if o.bill_paid_at else "Pay later · bill pending") if o.payment_due_at else "Paid online"

    @property
    def station_label(self) -> Optional[str]:
        return self.station_id or (self._order().station_id if self._order() else None)

    @property
    def customer_phone(self) -> Optional[str]:
        u = self._customer()
        return u.phone if u else None
