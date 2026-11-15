"""ORM model: otp challenge."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base
from utils.dates import now_utc

class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(20))           # LOGIN | ADMIN_2FA
    phone: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    expires_at: Mapped[dt.datetime]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
