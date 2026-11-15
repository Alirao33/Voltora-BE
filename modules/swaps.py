"""Business logic: swaps."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from constants.enums import OrderMode
from constants.orders import OPEN_ORDER
from models import Battery, Order, User
from modules.orders import complete_order, create_order
from schemas.order import OrderCreate
from schemas.swap import PhysicalSwapReturn, SwapRequest

def request_battery_swap(req: SwapRequest, user: User, db: Session):
    order = create_order(
        db,
        user=user,
        req=OrderCreate(
            station_id=req.station_id,
            mode=OrderMode.STATION,
            use_subscription=True,
        ),
    )
    return {
        "swap_id": order.order_id,
        "id": order.id,
        "station_id": order.station_id,
        "assigned_battery_id": order.pack_out_id,
        "slot_id": order.slot_id,
        "pickup_code": order.pickup_code,
        "status": order.status.value,
    }


def complete_physical_swap(data: PhysicalSwapReturn, user: User, db: Session):
    open_order = db.scalar(
        select(Order)
        .where(
            Order.user_id == user.id,
            Order.station_id == data.station_id,
            Order.status.in_(OPEN_ORDER),
        )
        .order_by(Order.id.desc())
    )
    if not open_order:
        raise HTTPException(404, "No active swap order found for this rider at this station")

    returned_pack = db.get(Battery, data.returned_battery_id)
    if returned_pack:
        returned_pack.soc = data.returned_soc
        returned_pack.soh = data.returned_soh
        returned_pack.remaining_ah = data.returned_soc / 100.0 * returned_pack.full_charge_capacity_ah
        returned_pack.full_charge_capacity_ah = data.returned_soh / 100.0 * returned_pack.rated_capacity_ah

    completed = complete_order(db, open_order, data.returned_battery_id)
    return {
        "status": "COMPLETED",
        "order_id": completed.order_id,
        "receipt_no": completed.receipt_no,
        "returned_pack": {
            "id": data.returned_battery_id,
            "soc": data.returned_soc,
            "soh": data.returned_soh,
        },
        "points_balance": user.points,
    }


def get_rider_swap_history(rider_id: str, user: User, db: Session):
    target_id = int(rider_id) if rider_id.isdigit() else user.id
    if target_id != user.id and not user.is_admin:
        raise HTTPException(403, "Cannot inspect another rider's swap history")
    orders = list(
        db.scalars(
            select(Order)
            .where(Order.user_id == target_id)
            .order_by(Order.id.desc())
            .limit(100)
        )
    )
    return {
        "rider_id": target_id,
        "history": [
            {
                "order_id": o.order_id,
                "station_id": o.station_id,
                "mode": o.mode.value,
                "status": o.status.value,
                "pack_out_id": o.pack_out_id,
                "pack_in_id": o.pack_in_id,
                "total_pkr": o.total_pkr,
                "receipt_no": o.receipt_no,
                "created_at": o.created_at.isoformat(),
                "completed_at": o.completed_at.isoformat() if o.completed_at else None,
            }
            for o in orders
        ],
    }
