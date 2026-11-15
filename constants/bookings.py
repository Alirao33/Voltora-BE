"""Constants: bookings."""

from __future__ import annotations

from constants.enums import BookingStatus, LockerStatus

ACTIVE_BOOKING = (BookingStatus.PENDING, BookingStatus.CONFIRMED)


BOOKABLE = (LockerStatus.AVAILABLE, LockerStatus.ONE_LEFT)
