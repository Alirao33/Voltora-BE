"""ORM model: locker."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from config.database import Base, enum_col
from constants.enums import DoorLockState, LockerStatus

class Locker(Base):
    __tablename__ = "lockers"
    __table_args__ = (UniqueConstraint("station_id", "door_number"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"), index=True)
    door_number: Mapped[str] = mapped_column(String(40))
    max_battery_capacity: Mapped[int] = mapped_column(Integer, default=2)
    current_occupied_batteries: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[LockerStatus] = mapped_column(enum_col(LockerStatus), default=LockerStatus.AVAILABLE)
    door_lock_state: Mapped[DoorLockState] = mapped_column(enum_col(DoorLockState), default=DoorLockState.LOCKED)
    reserved_for_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    reserved_booking_id: Mapped[Optional[int]] = mapped_column(Integer)

    @property
    def has_vacancy(self) -> bool:
        return self.current_occupied_batteries < self.max_battery_capacity
