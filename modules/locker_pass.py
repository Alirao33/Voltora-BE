"""Business logic: the fixed locker that comes with a locker pass."""

from __future__ import annotations

import re
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from constants.enums import LockerStatus, SubscriptionStatus
from models import Locker, Plan, Station, Subscription, User
from modules.audit import audit
from utils.dates import now_utc

FULL_MSG = "All lockers are currently full. Payment is paused until a locker is free, please try again later."


def _active(db: Session):
    return select(Subscription).where(Subscription.status == SubscriptionStatus.ACTIVE, Subscription.ends_at > now_utc())


def _serial(l: Locker) -> int:
    m = re.search(r"(\d+)\s*$", l.door_number or "")
    return int(m.group(1)) if m else 10**6


def free_lockers(db: Session, station_id: str) -> list[Locker]:
    """Lockers of a station not held by a live pass, in serial order. An expired or cancelled pass frees its locker."""
    taken = {s.locker_id for s in db.scalars(_active(db)) if s.locker_id}
    lockers = db.scalars(select(Locker).where(Locker.station_id == station_id, Locker.status != LockerStatus.MAINTENANCE))
    return sorted((l for l in lockers if l.id not in taken), key=_serial)


def _stations(db: Session) -> list[Station]:
    return list(db.scalars(select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False)).order_by(Station.id)))


def any_free_locker(db: Session) -> bool:
    return any(free_lockers(db, st.id) for st in _stations(db))


def require_free_locker(db: Session) -> None:
    if not any_free_locker(db):
        raise HTTPException(409, FULL_MSG)


def _status(s: Subscription) -> str:
    return "EXPIRED" if s.status == SubscriptionStatus.ACTIVE and s.ends_at <= now_utc() else s.status.value


def locker_pass(user: User, db: Session):
    subs = list(db.scalars(select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.id.desc())))
    plans = {p.id: p for p in db.scalars(select(Plan))}
    names = {st.id: st.name for st in db.scalars(select(Station))}
    history = [
        {
            "id": s.id,
            "plan_name": plans[s.plan_id].name if s.plan_id in plans else "Plan",
            "plan_code": plans[s.plan_id].code if s.plan_id in plans else None,
            "price_pkr": plans[s.plan_id].price_pkr if s.plan_id in plans else None,
            "starts_at": s.starts_at,
            "ends_at": s.ends_at,
            "status": _status(s),
            "station_id": s.station_id,
            "station_name": names.get(s.station_id),
            "locker_id": s.locker_id,
        }
        for s in subs
    ]
    current = next((h for h in history if h["status"] == "ACTIVE"), None)
    stations = [{"id": st.id, "name": st.name, "address": st.address, "free_lockers": len(free_lockers(db, st.id))} for st in _stations(db)]
    return {"current": current, "stations": stations, "history": history}


def assign_station(user: User, station_id: str, db: Session):
    """One-time: give the rider's live pass its station and the first free locker there."""
    sub = db.scalar(_active(db).where(Subscription.user_id == user.id).order_by(Subscription.ends_at.desc()))
    if not sub:
        raise HTTPException(404, "You need an active locker pass first")
    if sub.locker_id:
        raise HTTPException(409, "Your station is already set for this pass")
    if not db.get(Station, station_id):
        raise HTTPException(404, "Station not found")
    free = free_lockers(db, station_id)
    if not free:
        raise HTTPException(409, "This station's lockers are full. Please choose another station.")
    sub.station_id, sub.locker_id = station_id, free[0].id
    audit(db, user, "ASSIGN_LOCKER", "subscription", sub.id, {"station_id": station_id, "locker_id": free[0].id})
    db.commit()
    return locker_pass(user, db)
