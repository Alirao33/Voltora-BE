"""Business logic: stations."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Any, Optional

from constants.bookings import BOOKABLE
from constants.enums import AlertSeverity, AlertType, BatteryState, LockerStatus
from models import Battery, Locker, Station, User
from modules.alerts import raise_alert, resolve_alerts
from modules.lockers import availability, free_slots, lockers_at
from utils.geo import haversine_km

def station_stock(db: Session, station_id: str) -> dict[str, int]:
    rows = dict(
        db.execute(
            select(Battery.state, func.count())
            .where(Battery.station_id == station_id)
            .group_by(Battery.state)
        ).all()
    )
    get = lambda s: int(rows.get(s, 0))
    return {
        "charged": get(BatteryState.CHARGED),
        "charging": get(BatteryState.CHARGING),
        "depleted": get(BatteryState.DEPLETED),
        "reserved": get(BatteryState.RESERVED),
        "locked": get(BatteryState.LOCKED),
        "retired": get(BatteryState.RETIRED),
        "total": int(sum(rows.values())),
    }


def charged_count(db: Session, station_id: str) -> int:
    return station_stock(db, station_id)["charged"]


def check_stock_alert(db: Session, station: Optional[Station]) -> None:
    if not station or station.is_depot or not station.is_active:
        return
    stock = station_stock(db, station.id)
    if stock["charged"] == 0 and (station.target_stock > 0 or stock["total"] > 0):
        raise_alert(
            db,
            AlertType.STOCK_OUT,
            AlertSeverity.CRITICAL,
            f"{station.name}: no charged packs left",
            station_id=station.id,
        )
    elif stock["charged"] > 0:
        resolve_alerts(db, AlertType.STOCK_OUT, station_id=station.id)


def station_view(db: Session, st: Station, include_lockers: bool = False) -> dict[str, Any]:
    lockers = lockers_at(db, st.id)
    counts = {s.value.lower(): 0 for s in LockerStatus}
    for l in lockers:
        counts[l.status.value.lower()] += 1
    free = sum(l.max_battery_capacity - l.current_occupied_batteries for l in lockers if l.status in BOOKABLE)
    out: dict[str, Any] = {
        "id": st.id,
        "name": st.name,
        "latitude": st.latitude,
        "longitude": st.longitude,
        "address": st.address,
        "total_lockers": st.total_lockers,
        "occupancy_status": availability(free),
        "free_slots": free,
        "lockers_registered": len(lockers),
        "locker_status_counts": counts,
        "is_active": st.is_active,
        "is_depot": st.is_depot,
        "target_stock": st.target_stock,
        "host_name": st.host_name,
        "stock": station_stock(db, st.id),
    }
    if include_lockers:
        out["lockers"] = lockers
    return out


def rank_public_sites(db: Session, lat: float, lon: float, max_km: float) -> list[tuple[float, Station, int]]:
    ranked = []
    for st in db.scalars(select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))):
        free = free_slots(db, st.id)
        if free <= 0:
            continue
        d = haversine_km(lat, lon, st.latitude, st.longitude)
        if d <= max_km:
            ranked.append((d, st, free))
    ranked.sort(key=lambda t: (t[0], t[1].id))
    return ranked


def nearest_vip_locker(db: Session, user: User, lat: float, lon: float, max_km: float):
    best = None
    for l in db.scalars(select(Locker).where(Locker.reserved_for_user_id == user.id)):
        if l.status == LockerStatus.MAINTENANCE or not l.has_vacancy:
            continue
        st = db.get(Station, l.station_id)
        if not st or not st.is_active:
            continue
        d = haversine_km(lat, lon, st.latitude, st.longitude)
        if d <= max_km and (best is None or d < best[0]):
            best = (d, st, l.id)
    return best


def stock_ratio(db: Session, st: Station) -> Optional[float]:
    return station_stock(db, st.id)["charged"] / st.target_stock if st.target_stock > 0 else None


def resolve_station(db: Session, station_id: Optional[str], lat: Optional[float], lon: Optional[float]) -> Station:
    if station_id:
        st = db.get(Station, station_id)
        if not st:
            raise HTTPException(404, f"Station {station_id} not found")
        return st
    best = None
    for st in db.scalars(select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))):
        if station_stock(db, st.id)["charged"] <= 0:
            continue
        d = haversine_km(lat, lon, st.latitude, st.longitude)
        if d <= 25 and (best is None or d < best[0]):
            best = (d, st)
    if not best:
        raise HTTPException(404, "No station with a charged pack within 25 km")
    return best[1]
