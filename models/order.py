"""ORM model: order."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base, enum_col
from constants.enums import OrderMode, OrderStatus
from constants.orders import LATE_PENALTY_RATE
from utils.dates import now_utc

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    guest_ref: Mapped[Optional[str]] = mapped_column(String(40))
    guest_name: Mapped[Optional[str]] = mapped_column(String(80))
    guest_phone: Mapped[Optional[str]] = mapped_column(String(20))
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"), index=True)
    mode: Mapped[OrderMode] = mapped_column(enum_col(OrderMode))
    status: Mapped[OrderStatus] = mapped_column(enum_col(OrderStatus), default=OrderStatus.PENDING_PAYMENT, index=True)
    plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plans.id"))
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id"))
    pack_out_id: Mapped[Optional[str]] = mapped_column(ForeignKey("batteries.id"))
    pack_in_id: Mapped[Optional[str]] = mapped_column(ForeignKey("batteries.id"))
    booking_id: Mapped[Optional[int]] = mapped_column(Integer)
    slot_id: Mapped[Optional[str]] = mapped_column(String(40))             # slot / locker shown to the rider
    pickup_code: Mapped[Optional[str]] = mapped_column(String(8))          # unlock PIN shown in the app
    price: Mapped[dict] = mapped_column(JSON, default=dict)
    total_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    charged_pkr: Mapped[float] = mapped_column(Float, default=0.0)         # upfront amount paid
    actual_total_pkr: Mapped[Optional[float]] = mapped_column(Float)       # recomputed upon old pack return
    settlement_delta_pkr: Mapped[Optional[float]] = mapped_column(Float)
    delivery_address: Mapped[Optional[str]] = mapped_column(String(300))
    delivery_lat: Mapped[Optional[float]] = mapped_column(Float)
    delivery_lon: Mapped[Optional[float]] = mapped_column(Float)
    delivery_otp: Mapped[Optional[str]] = mapped_column(String(8))
    rating: Mapped[Optional[int]] = mapped_column(Integer)
    review: Mapped[Optional[str]] = mapped_column(String(500))
    receipt_no: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    hold_expires_at: Mapped[Optional[dt.datetime]]
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc, index=True)
    paid_at: Mapped[Optional[dt.datetime]]
    completed_at: Mapped[Optional[dt.datetime]]
    payment_due_at: Mapped[Optional[dt.datetime]]                          # set only for pay-later bills
    bill_paid_at: Mapped[Optional[dt.datetime]]
    penalty_pkr: Mapped[float] = mapped_column(Float, default=0.0)         # late penalty actually paid

    def _delivery_driver(self):
        """(van, driver) of the active delivery task, or (None, None) before admin approval."""
        from sqlalchemy import select
        from sqlalchemy.orm import object_session
        from constants.enums import TaskKind, TaskStatus
        from models.user import User
        from models.van import Van
        from models.van_task import VanTask

        db = object_session(self)
        if db is None or self.mode != OrderMode.HOME:
            return None, None
        van = db.scalar(
            select(Van)
            .join(VanTask, VanTask.van_id == Van.id)
            .where(VanTask.order_id == self.id, VanTask.kind == TaskKind.DELIVERY, VanTask.status != TaskStatus.CANCELLED)
            .order_by(VanTask.id.desc())
        )
        driver = db.get(User, van.driver_id) if van and van.driver_id else None
        return van, driver

    @property
    def van_name(self) -> Optional[str]:
        van, _ = self._delivery_driver()
        return van.name if van else None

    @property
    def driver_name(self) -> Optional[str]:
        _, d = self._delivery_driver()
        return (d.full_name or d.username) if d else None

    delivery_stage: Mapped[Optional[str]] = mapped_column(String(20))   # PICKED_UP | EN_ROUTE, set by the driver; None = accepted
    locker_code_requested_at: Mapped[Optional[dt.datetime]]   # delivery man asked to see the locker code
    locker_code_shared_at: Mapped[Optional[dt.datetime]]      # rider allowed it

    @property
    def locker_code_shared(self) -> bool:
        return self.locker_code_shared_at is not None

    @property
    def locker_code_requested(self) -> bool:
        return self.locker_code_requested_at is not None and self.locker_code_shared_at is None

    @property
    def driver_phone(self) -> Optional[str]:
        _, d = self._delivery_driver()
        return d.phone if d else None

    @property
    def _bill_base_pkr(self) -> float:
        return self.actual_total_pkr if self.actual_total_pkr is not None else self.total_pkr

    @property
    def bill_penalty_pkr(self) -> float:
        if self.payment_due_at is None:
            return 0.0
        if self.bill_paid_at:
            return self.penalty_pkr or 0.0
        return round(self._bill_base_pkr * LATE_PENALTY_RATE, 2) if now_utc() > self.payment_due_at else 0.0

    @property
    def bill_due_pkr(self) -> Optional[float]:
        """Amount still owed on a pay-later bill, or None when there is nothing to pay."""
        if self.payment_due_at is None or self.bill_paid_at or self.status in (
            OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.FAILED
        ):
            return None
        return round(self._bill_base_pkr + self.bill_penalty_pkr, 2)

    @property
    def order_id(self) -> str:
        return f"ORD-{self.id}"
