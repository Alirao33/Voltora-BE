"""Business logic: lockers."""

from __future__ import annotations

import secrets
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Optional

from constants.bookings import ACTIVE_BOOKING, BOOKABLE
from constants.enums import ActionType, LockerStatus, PlanKind, SiteAvailability
from models import Booking, Locker, Plan, Station, User
from modules.audit import audit
from modules.pricing import calculate_locker_plan_price
from schemas.locker import LockerCreate, LockerRateUpdateRequest, LockerUpdate
from utils.access import scoped_station_id

def derive_status(l: Locker) -> LockerStatus:
    if l.status == LockerStatus.MAINTENANCE:
        return LockerStatus.MAINTENANCE
    if l.reserved_for_user_id is not None:
        return LockerStatus.RESERVED_VIP
    free = l.max_battery_capacity - l.current_occupied_batteries
    if free <= 0:
        return LockerStatus.FULL
    return LockerStatus.ONE_LEFT if free == 1 and l.max_battery_capacity > 1 else LockerStatus.AVAILABLE


def refresh_status(l: Locker) -> None:
    l.status = derive_status(l)


def lockers_at(db: Session, station_id: str, lock: bool = False) -> list[Locker]:
    q = select(Locker).where(Locker.station_id == station_id).order_by(Locker.id)
    return list(db.scalars(q.with_for_update() if lock else q))


def free_slots(db: Session, station_id: str) -> int:
    return sum(
        l.max_battery_capacity - l.current_occupied_batteries
        for l in lockers_at(db, station_id)
        if l.status in BOOKABLE
    )


def availability(free: int) -> SiteAvailability:
    return (
        SiteAvailability.FULL
        if free <= 0
        else SiteAvailability.ONE_LEFT
        if free == 1
        else SiteAvailability.AVAILABLE
    )


def new_unlock_code(db: Session, locker_id: str) -> str:
    in_use = set(
        db.scalars(
            select(Booking.unlock_code).where(
                Booking.locker_id == locker_id, Booking.status.in_(ACTIVE_BOOKING)
            )
        )
    )
    while True:
        code = f"{secrets.randbelow(10000):04d}"
        if code not in in_use:
            return code


def pick_locker(db: Session, station_id: str, user: Optional[User], action: ActionType) -> Locker:
    lockers = lockers_at(db, station_id, lock=True)
    if action == ActionType.SWAP_DROP_AND_PICK and user:
        mine = [
            l
            for l in lockers
            if l.reserved_for_user_id == user.id
            and l.status != LockerStatus.MAINTENANCE
            and l.has_vacancy
        ]
        if mine:
            return min(mine, key=lambda l: (l.current_occupied_batteries, l.id))
    public = [l for l in lockers if l.status in BOOKABLE and l.has_vacancy]
    if not public:
        raise HTTPException(409, "No available locker at this site (full, reserved or under maintenance)")
    return min(public, key=lambda l: (l.current_occupied_batteries, l.id))


def create_locker(body: LockerCreate, actor: User, db: Session):
    scoped_station_id(actor, body.site_id)
    if not db.get(Station, body.site_id):
        raise HTTPException(404, f"Site {body.site_id} not found")
    if body.reserved_for_user_id and not db.get(User, body.reserved_for_user_id):
        raise HTTPException(404, "VIP user not found")
    lid = body.id or f"LKR-{secrets.token_hex(3).upper()}"
    if db.get(Locker, lid):
        raise HTTPException(409, f"Locker {lid} already exists")
    existing = db.scalar(select(func.count()).select_from(Locker).where(Locker.station_id == body.site_id))
    locker = Locker(
        id=lid,
        station_id=body.site_id,
        door_number=body.door_number or f"Door {existing + 1:02d}",
        max_battery_capacity=body.max_battery_capacity,
        current_occupied_batteries=body.current_occupied_batteries,
        status=body.status,
        door_lock_state=body.door_lock_state,
        reserved_for_user_id=body.reserved_for_user_id,
    )
    refresh_status(locker)
    db.add(locker)
    audit(db, actor, "CREATE_LOCKER", "locker", lid, {"site_id": body.site_id})
    db.commit()
    return locker


def update_locker(locker_id: str, body: LockerUpdate, actor: User, db: Session):
    l = db.get(Locker, locker_id)
    if not l:
        raise HTTPException(404, "Locker not found")
    scoped_station_id(actor, l.station_id)
    if body.max_battery_capacity is not None:
        if body.max_battery_capacity < l.current_occupied_batteries:
            raise HTTPException(409, "New capacity is less than current occupancy")
        l.max_battery_capacity = body.max_battery_capacity
    if body.door_lock_state is not None:
        l.door_lock_state = body.door_lock_state
    if body.status == LockerStatus.MAINTENANCE:
        l.status = LockerStatus.MAINTENANCE
    elif body.status == LockerStatus.AVAILABLE and l.status == LockerStatus.MAINTENANCE:
        l.status = LockerStatus.AVAILABLE
        refresh_status(l)
    else:
        refresh_status(l)
    audit(db, actor, "UPDATE_LOCKER", "locker", locker_id, body.model_dump(exclude_unset=True, mode="json"))
    db.commit()
    return l


def update_locker_rates(req: LockerRateUpdateRequest, actor: User, db: Session):
    tiers = ["monthly", "half_yearly", "yearly"]
    updated_plans = []

    for duration in tiers:
        new_price = calculate_locker_plan_price(
            duration_type=duration,
            base_daily_rate=req.base_daily_rate,
            discount_factor=req.discount_factor,
        )
        plan = db.scalar(
            select(Plan).where(Plan.kind == PlanKind.LOCKER, Plan.duration_type == duration)
        )
        if plan:
            plan.price_pkr = new_price
            updated_plans.append(
                {
                    "code": plan.code,
                    "name": plan.name,
                    "duration": duration,
                    "new_price_pkr": new_price,
                }
            )

    audit(
        db,
        actor,
        "UPDATE_LOCKER_RATES",
        "plan",
        "LOCKER",
        {"base_daily_rate": req.base_daily_rate, "discount_factor": req.discount_factor},
    )
    db.commit()
    return {
        "status": "SUCCESS",
        "message": f"Locker plans updated using base rate {req.base_daily_rate} PKR/day",
        "updated_plans": updated_plans,
    }
