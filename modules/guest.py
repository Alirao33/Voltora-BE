"""Business logic: guest."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from constants.enums import OrderMode
from models import Battery, Plan, Station
from modules.bookings import expire_stale_bookings
from modules.orders import create_order
from modules.pricing import calculate_price, demand_factor, get_pricing_config
from modules.stations import resolve_station, station_stock, station_view, stock_ratio
from schemas.guest import GuestQuoteRequest, GuestSwapRequest
from schemas.locker import LockerOut
from schemas.order import OrderCreate
from utils.dates import now_utc

def public_stations(db: Session):
    expire_stale_bookings(db)
    db.commit()
    return [
        station_view(db, st)
        for st in db.scalars(
            select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False)).order_by(Station.id)
        )
    ]


def public_station(station_id: str, db: Session):
    expire_stale_bookings(db)
    db.commit()
    st = db.get(Station, station_id)
    if not st or not st.is_active or st.is_depot:
        raise HTTPException(404, "Station not found")
    view = station_view(db, st, include_lockers=True)
    view["lockers"] = [LockerOut.model_validate(l) for l in view["lockers"]]
    return view


def public_plans(db: Session):
    return list(db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.id)))


def guest_quote(body: GuestQuoteRequest, db: Session):
    st = resolve_station(db, body.site_id, body.latitude, body.longitude)
    pack = db.get(Battery, body.pack_id) if body.pack_id else None
    if body.pack_id and not pack:
        raise HTTPException(404, f"Pack {body.pack_id} not found")
    cfg = get_pricing_config(db)
    price = calculate_price(
        voltage=body.voltage or (pack.nominal_voltage if pack else 48.0),
        capacity_used_ah=body.capacity_used_ah,
        chemistry=pack.chemistry if pack else body.chemistry,
        delivery=body.delivery,
        demand=demand_factor(now_utc(), stock_ratio(db, st), cfg),
        cfg=cfg,
        returned_temp_c=body.temperature_c if body.temperature_c is not None else (pack.temperature_c if pack else None),
        is_subscribed=body.is_subscribed,
        plan_code=body.plan_code,
    )
    return {
        "station_id": st.id,
        "station_name": st.name,
        "charged_packs_available": station_stock(db, st.id)["charged"],
        "price": price,
    }


def guest_pay_and_swap(body: GuestSwapRequest, db: Session):
    st = resolve_station(db, body.site_id, body.latitude, body.longitude)
    cfg = get_pricing_config(db)
    expected = calculate_price(
        voltage=body.voltage or 48.0,
        capacity_used_ah=body.capacity_used_ah,
        chemistry=body.chemistry,
        delivery=body.delivery or body.mode == OrderMode.HOME,
        demand=demand_factor(now_utc(), stock_ratio(db, st), cfg),
        cfg=cfg,
    ).total_pkr
    if body.amount_paid_pkr is not None and body.amount_paid_pkr + 0.01 < expected:
        raise HTTPException(402, f"Pay first: PKR {expected:.2f} due, received PKR {body.amount_paid_pkr:.2f}")
    req = OrderCreate(
        station_id=st.id,
        mode=OrderMode.HOME if body.delivery else body.mode,
        use_subscription=False,
        capacity_used_ah=body.capacity_used_ah,
        payment_method=body.payment_method,
        payment_reference=body.payment_reference,
        delivery_address=body.delivery_address,
        delivery_lat=body.delivery_lat,
        delivery_lon=body.delivery_lon,
    )
    return create_order(
        db,
        user=None,
        req=req,
        guest_name=body.guest_name,
        guest_phone=body.phone,
        capacity_used_ah=body.capacity_used_ah,
    )
