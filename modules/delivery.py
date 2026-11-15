"""Business logic: delivery."""

from __future__ import annotations

import secrets
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from constants.enums import BatteryState, Chemistry, OrderMode, PaymentMethod, TaskKind, TaskStatus
from constants.orders import OPEN_ORDER
from models import Battery, Order, Station, User, VanTask
from modules.audit import audit
from modules.fleet import confirm_task
from modules.orders import complete_order, create_order
from modules.pricing import get_pricing_config
from schemas.delivery import AddressValidationRequest, BatteryOrderRequest, DeliveryHandoverOTP
from schemas.order import OrderCreate
from utils.access import scoped_station_id
from utils.geo import haversine_km

def validate_delivery_address(req: AddressValidationRequest, db: Session):
    stations = list(db.scalars(select(Station).where(Station.is_active.is_(True))))
    if not stations:
        return {"is_serviceable": False, "message": "No active service stations configured."}
    nearest = min(stations, key=lambda s: haversine_km(req.latitude, req.longitude, s.latitude, s.longitude))
    dist_km = round(haversine_km(req.latitude, req.longitude, nearest.latitude, nearest.longitude), 2)
    is_serviceable = dist_km <= 25.0
    cfg = get_pricing_config(db)
    return {
        "is_serviceable": is_serviceable,
        "nearest_station_id": nearest.id,
        "nearest_station_name": nearest.name,
        "distance_km": dist_km,
        "estimated_delivery_fee_pkr": cfg.delivery_cost_pkr if is_serviceable else None,
        "message": (
            f"Address is within {dist_km} km of {nearest.name}."
            if is_serviceable
            else f"Location is {dist_km} km away (outside 25 km delivery radius)."
        ),
    }


def get_battery_catalogue(db: Session):
    cfg = get_pricing_config(db)
    lfp_count = db.scalar(
        select(func.count())
        .select_from(Battery)
        .where(Battery.chemistry == Chemistry.LFP, Battery.state == BatteryState.CHARGED)
    ) or 0
    nmc_count = db.scalar(
        select(func.count())
        .select_from(Battery)
        .where(Battery.chemistry == Chemistry.NMC, Battery.state == BatteryState.CHARGED)
    ) or 0
    return {
        "batteries": [
            {
                "id": "VOLTORA-LFP-48V",
                "name": "Voltora LFP 48V 20Ah Pack",
                "chemistry": Chemistry.LFP.value,
                "nominal_voltage": 48.0,
                "rated_capacity_ah": 20.0,
                "wear_rate_pkr_per_kwh": cfg.wear_pkr_per_kwh_lfp,
                "charged_available": int(lfp_count),
            },
            {
                "id": "VOLTORA-NMC-48V",
                "name": "Voltora NMC 48V 20Ah High-Density Pack",
                "chemistry": Chemistry.NMC.value,
                "nominal_voltage": 48.0,
                "rated_capacity_ah": 20.0,
                "wear_rate_pkr_per_kwh": cfg.wear_pkr_per_kwh_nmc,
                "charged_available": int(nmc_count),
            },
        ]
    }


def create_battery_delivery_order(order: BatteryOrderRequest, user: User, db: Session):
    pay_method = PaymentMethod.WALLET
    if order.payment_method.upper() in PaymentMethod.__members__:
        pay_method = PaymentMethod[order.payment_method.upper()]
    created = create_order(
        db,
        user=user,
        req=OrderCreate(
            latitude=order.latitude,
            longitude=order.longitude,
            mode=OrderMode.HOME,
            use_subscription=order.is_locker_customer,
            payment_method=pay_method,
            payment_reference=f"APP-{secrets.token_hex(6).upper()}",
            delivery_address=order.delivery_address,
            delivery_lat=order.latitude,
            delivery_lon=order.longitude,
        ),
    )
    return {
        "order_id": created.order_id,
        "id": created.id,
        "status": created.status.value,
        "delivery_otp": created.delivery_otp,
        "total_pkr": created.total_pkr,
        "charged_pkr": created.charged_pkr,
        "station_id": created.station_id,
        "message": "Battery delivery order placed. It will be dispatched to a van once an admin approves it.",
    }


def get_delivery_zones(actor: User, db: Session):
    cfg = get_pricing_config(db)
    target_station = scoped_station_id(actor)
    st_q = select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))
    if target_station:
        st_q = st_q.where(Station.id == target_station)

    zones = []
    for st in db.scalars(st_q):
        zones.append(
            {
                "zone_id": f"ZONE-{st.id}",
                "station_id": st.id,
                "name": st.name,
                "latitude": st.latitude,
                "longitude": st.longitude,
                "radius_km": 25.0,
                "delivery_fee_pkr": cfg.delivery_cost_pkr,
                "is_active": st.is_active,
            }
        )
    return {"zones": zones}


def complete_delivery_handover(handover: DeliveryHandoverOTP, user: User, db: Session):
    raw_id = handover.order_id.strip().upper()
    numeric_id = int(raw_id.replace("ORD-", "")) if raw_id.startswith("ORD-") else int(raw_id)

    order = db.get(Order, numeric_id)
    if not order:
        raise HTTPException(404, f"Order {handover.order_id} not found")
    if order.mode != OrderMode.HOME:
        raise HTTPException(400, "Handover OTP applies only to HOME delivery orders")
    if order.status not in OPEN_ORDER:
        raise HTTPException(409, f"Order is already {order.status.value}")
    if not order.delivery_otp or not secrets.compare_digest(
        handover.otp_code.strip(), order.delivery_otp
    ):
        raise HTTPException(403, "Invalid rider delivery OTP")

    task = db.scalar(
        select(VanTask).where(
            VanTask.order_id == order.id,
            VanTask.kind == TaskKind.DELIVERY,
            VanTask.status == TaskStatus.PENDING,
        )
    )
    if task:
        confirm_task(
            db,
            task,
            user,
            delivery_otp=handover.otp_code.strip(),
            returned_pack_id=handover.returned_pack_id,
        )
    else:
        complete_order(db, order, handover.returned_pack_id)

    audit(
        db,
        user,
        "DELIVERY_HANDOVER",
        "order",
        order.id,
        {"cash_collected_pkr": handover.cash_collected_amount, "returned_pack_id": handover.returned_pack_id},
    )
    db.commit()
    return {
        "order_id": order.order_id,
        "status": "DELIVERED",
        "cash_collected_pkr": handover.cash_collected_amount,
        "receipt_no": order.receipt_no,
        "message": "Battery handover verified and swap order completed.",
    }
