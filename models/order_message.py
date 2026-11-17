"""ORM model: a text message between the rider and the delivery man of a home delivery."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base
from utils.dates import now_utc


class OrderMessage(Base):
    __tablename__ = "order_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    sender_id: Mapped[int] = mapped_column(Integer)
    sender_role: Mapped[str] = mapped_column(String(10))  # RIDER | DRIVER
    body: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
