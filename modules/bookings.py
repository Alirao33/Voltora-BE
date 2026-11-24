"""Business logic: bookings."""

from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Optional

from config.settings import get_settings
from constants.bookings import ACTIVE_BOOKING
from constants.enums import ActionType, BookingStatus, LockerStatus, RiderTier
from models import Booking, Locker, Station, User
from modules.lockers import new_unlock_code, pick_locker, refresh_status
from modules.stations import nearest_vip_locker, rank_public_sites, station_view
from modules.subscriptions import active_subscription
from schemas.booking import BookingActionRequest, BookingOut, BookLockerRequest, SmartSwapRequest
from utils.dates import ensure_aware, now_utc

def release_booking(db: Session, b: Booking, new_status: BookingStatus) -> None:
    if b.status not in ACTIVE_BOOKING:
        return
    b.status = new_status
    l = db.get(Locker, b.locker_id)
    if not l:
        return
    if b.action_type == ActionType.RESERVE_VIP_SLOT:
        if l.reserved_booking_id == b.id:
            l.reserved_for_user_id = None
            l.reserved_booking_id = None
    else:
        l.current_occupied_batteries = max(0, l.current_occupied_batteries - 1)
    refresh_status(l)


def expire_stale_bookings(db: Session, now: Optional[datetime] = None) -> int:
    """Release standalone slot holds past their grace window."""
    now = now or now_utc()
    n = 0
    for b in db.scalars(
        select(Booking).where(
            Booking.status.in_(ACTIVE_BOOKING),
            Booking.hold_expires_at < now,
            Booking.order_id.is_(None),
        )
    ):
        release_booking(db, b, BookingStatus.EXPIRED)
        n += 1
    return n


def place_booking(
    db: Session,
    *,
    user: Optional[User],
    guest_ref: Optional[str] = None,
    station_id: str,
    locker_id: Optional[str] = None,
    action: ActionType = ActionType.SWAP_DROP_AND_PICK,
    scheduled_time: Optional[datetime] = None,
    status: BookingStatus = BookingStatus.CONFIRMED,
    order_id: Optional[int] = None,
    code: Optional[str] = None,
) -> Booking:
    s = get_settings()
    now = now_utc()
    when = ensure_aware(scheduled_time) if scheduled_time else now
    if when < now - timedelta(minutes=5):
        raise HTTPException(422, "scheduled_time is in the past")
    if when > now + timedelta(hours=s.max_booking_advance_hours):
        raise HTTPException(422, f"Bookings can be made at most {s.max_booking_advance_hours} hours ahead")

    expire_stale_bookings(db, now)
    st = db.get(Station, station_id)
    if not st:
        raise HTTPException(404, f"Site {station_id} not found")
    if not st.is_active:
        raise HTTPException(409, "Station is not active")
    if action == ActionType.RESERVE_VIP_SLOT and not (user and user.is_vip):
        raise HTTPException(403, "Only VIP riders can reserve a VIP locker")
    if user and action in (ActionType.SWAP_DROP_AND_PICK, ActionType.RESERVE_VIP_SLOT):
        if db.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.user_id == user.id,
                Booking.action_type == action,
                Booking.status.in_(ACTIVE_BOOKING),
            )
        ):
            raise HTTPException(409, f"You already have an active {action.value} booking")

    if locker_id:
        locker = db.get(Locker, locker_id, with_for_update=True)
        if not locker:
            raise HTTPException(404, f"Locker {locker_id} not found")
        if locker.station_id != station_id:
            raise HTTPException(400, "Locker does not belong to this site")
    else:
        locker = pick_locker(db, station_id, user, action)

    if locker.status == LockerStatus.MAINTENANCE:
        raise HTTPException(409, "Locker is under maintenance")
    if not locker.has_vacancy:
        raise HTTPException(409, "Locker is full")
    if locker.reserved_for_user_id is not None and (not user or locker.reserved_for_user_id != user.id):
        raise HTTPException(409, "Locker is reserved for a VIP rider")
    if action == ActionType.RESERVE_VIP_SLOT and locker.reserved_for_user_id is not None:
        raise HTTPException(409, "Locker is already reserved")

    b = Booking(
        user_id=user.id if user else None,
        guest_ref=guest_ref,
        station_id=station_id,
        locker_id=locker.id,
        action_type=action,
        scheduled_time=when,
        hold_expires_at=when + timedelta(minutes=s.booking_grace_minutes),
        unlock_code=code or new_unlock_code(db, locker.id),
        status=status,
        order_id=order_id,
    )
    db.add(b)
    db.flush()
    if action == ActionType.RESERVE_VIP_SLOT:
        locker.reserved_for_user_id = user.id
        locker.reserved_booking_id = b.id
    else:
        locker.current_occupied_batteries += 1
    refresh_status(locker)
    return b


def _own_booking(db: Session, user: User, booking_id: int) -> Booking:
    b = db.get(Booking, booking_id)
    if not b or (b.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Booking not found")
    return b


def book_locker(body: BookLockerRequest, user: User, db: Session):
    b = place_booking(
        db,
        user=user,
        station_id=body.site_id,
        locker_id=body.locker_id,
        action=body.action_type,
        scheduled_time=body.scheduled_time,
    )
    db.commit()
    db.refresh(b)
    return b


def smart_swap(body: SmartSwapRequest, user: User, db: Session):
    expire_stale_bookings(db)
    vip = nearest_vip_locker(db, user, body.latitude, body.longitude, body.max_distance_km)
    if vip:
        dist_km, st, locker_id = vip
        tier = RiderTier.VIP
    else:
        ranked = rank_public_sites(db, body.latitude, body.longitude, body.max_distance_km)
        if not ranked:
            raise HTTPException(404, f"No site with a free locker within {body.max_distance_km} km")
        dist_km, st, _ = ranked[0]
        locker_id = None
        tier = RiderTier.SUBSCRIBED if active_subscription(db, user) else RiderTier.GUEST

    booking = None
    if body.auto_book:
        booking = place_booking(
            db,
            user=user,
            station_id=st.id,
            locker_id=locker_id,
            action=ActionType.SWAP_DROP_AND_PICK,
            scheduled_time=body.scheduled_time,
        )
        locker_id = booking.locker_id
    db.commit()
    return {
        "rider_tier": tier,
        "site": station_view(db, st),
        "distance_km": round(dist_km, 2),
        "locker_id": locker_id,
        "booking": BookingOut.model_validate(booking) if booking else None,
    }


def my_bookings(user: User, db: Session):
    expire_stale_bookings(db)
    db.commit()
    return list(db.scalars(select(Booking).where(Booking.user_id == user.id).order_by(Booking.id.desc())))


def confirm_booking(booking_id: int, body: BookingActionRequest, user: User, db: Session):
    b = _own_booking(db, user, booking_id)
    if b.status != BookingStatus.PENDING:
        raise HTTPException(409, f"Booking is {b.status.value}")
    if not body.unlock_code or body.unlock_code != b.unlock_code:
        raise HTTPException(403, "Invalid unlock code")
    b.status = BookingStatus.CONFIRMED
    db.commit()
    return b


def cancel_booking(booking_id: int, user: User, db: Session):
    b = _own_booking(db, user, booking_id)
    if b.status not in ACTIVE_BOOKING:
        raise HTTPException(409, f"Booking is {b.status.value}")
    release_booking(db, b, BookingStatus.CANCELLED)
    db.commit()
    return b


def get_booking(booking_id: int, user: User, db: Session):
    expire_stale_bookings(db)
    db.commit()
    return _own_booking(db, user, booking_id)


def complete_booking(booking_id: int, user: User, db: Session):
    b = _own_booking(db, user, booking_id)
    if b.status != BookingStatus.CONFIRMED:
        raise HTTPException(409, f"Booking is {b.status.value}, unlock it first")
    release_booking(db, b, BookingStatus.COMPLETED)
    db.commit()
    return b
