"""Business logic: sites."""

from __future__ import annotations

import secrets
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Locker, Station, User
from modules.audit import audit
from modules.bookings import expire_stale_bookings
from modules.stations import station_view
from schemas.locker import LockerOut
from schemas.station import StationCreate, StationCreateRequest, StationUpdate
from utils.access import scoped_station_id

def list_sites(actor: User, db: Session):
    expire_stale_bookings(db)
    db.commit()
    target_station = scoped_station_id(actor)
    q = select(Station).order_by(Station.id)
    if target_station:
        q = q.where(Station.id == target_station)
    out = []
    for st in db.scalars(q):
        v = station_view(db, st, include_lockers=True)
        v["lockers"] = [LockerOut.model_validate(l) for l in v["lockers"]]
        out.append(v)
    return out


def create_site(body: StationCreate, actor: User, db: Session):
    sid = body.id or f"STATION-{secrets.token_hex(3).upper()}"
    if db.get(Station, sid):
        raise HTTPException(409, f"Site {sid} already exists")
    st = Station(
        id=sid,
        name=body.name,
        latitude=body.latitude,
        longitude=body.longitude,
        address=body.address,
        total_lockers=body.total_lockers,
        host_name=body.host_name,
        host_revenue_share_pct=body.host_revenue_share_pct,
        is_depot=body.is_depot,
    )
    db.add(st)
    audit(db, actor, "CREATE_SITE", "station", sid, {"name": body.name})
    db.commit()
    return station_view(db, st, include_lockers=True)


def create_station_portal(req: StationCreateRequest, actor: User, db: Session):
    sid = f"STN-{secrets.token_hex(3).upper()}"
    st = Station(
        id=sid,
        name=req.name,
        latitude=req.latitude,
        longitude=req.longitude,
        address=f"{req.address}, {req.city}",
        total_lockers=req.total_lockers,
        is_active=(req.status.upper() == "OPERATIONAL"),
        target_stock=max(2, req.total_lockers),
    )
    db.add(st)
    db.flush()
    for d in range(1, req.total_lockers + 1):
        db.add(Locker(id=f"{sid}-L{d}", station_id=sid, door_number=f"Door {d:02d}"))
    audit(db, actor, "CREATE_STATION", "station", sid, {"name": req.name, "lockers": req.total_lockers})
    db.commit()
    return {
        "station_id": sid,
        "status": "CREATED",
        "details": station_view(db, st, include_lockers=True),
    }


def update_site(station_id: str, body: StationUpdate, actor: User, db: Session):
    scoped_station_id(actor, station_id)
    st = db.get(Station, station_id)
    if not st:
        raise HTTPException(404, "Site not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(st, k, v)
    audit(db, actor, "UPDATE_SITE", "station", station_id, changes)
    db.commit()
    return station_view(db, st)
