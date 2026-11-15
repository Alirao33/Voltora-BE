"""Thin controllers (HTTP in, module call, HTTP out): bookings."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import bookings as bookings_service
from schemas.booking import BookingActionRequest, BookLockerRequest, SmartSwapRequest

def book_locker(body: BookLockerRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return bookings_service.book_locker(body=body, user=user, db=db)


def smart_swap(body: SmartSwapRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return bookings_service.smart_swap(body=body, user=user, db=db)


def my_bookings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return bookings_service.my_bookings(user=user, db=db)


def confirm_booking(booking_id: int, body: BookingActionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return bookings_service.confirm_booking(booking_id=booking_id, body=body, user=user, db=db)


def cancel_booking(booking_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return bookings_service.cancel_booking(booking_id=booking_id, user=user, db=db)
