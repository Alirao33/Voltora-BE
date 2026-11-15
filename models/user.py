"""ORM model: user."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base, enum_col
from constants.enums import Role
from utils.dates import now_utc

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(120))
    hashed_password: Mapped[Optional[str]] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(enum_col(Role), default=Role.RIDER)
    station_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("stations.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
    last_login_at: Mapped[Optional[dt.datetime]]

    @property
    def is_admin(self) -> bool:
        return self.role in {Role.ADMIN, Role.STATION_ADMIN}

    @property
    def is_super_admin(self) -> bool:
        return self.role == Role.ADMIN
