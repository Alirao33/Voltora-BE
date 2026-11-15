"""ORM model: plan."""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base, enum_col
from constants.enums import PlanKind

class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[PlanKind] = mapped_column(enum_col(PlanKind))
    swaps_included: Mapped[int] = mapped_column(Integer, default=0)
    price_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    duration_type: Mapped[str] = mapped_column(String(20), default="monthly")  # 'weekly', 'monthly', 'yearly'
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
