"""Business logic: rider <-> delivery man handover (text chat and the locker-code consent)."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from constants.enums import OrderMode, OrderStatus, TaskKind, TaskStatus
from models import Order, OrderMessage, User, Van, VanTask
from modules.audit import audit
from utils.dates import now_utc

CLOSED = (OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.FAILED)


def _delivery_driver_id(db: Session, order: Order):
    van = db.scalar(
        select(Van)
        .join(VanTask, VanTask.van_id == Van.id)
        .where(VanTask.order_id == order.id, VanTask.kind == TaskKind.DELIVERY, VanTask.status != TaskStatus.CANCELLED)
        .order_by(VanTask.id.desc())
    )
    return van.driver_id if van else None


def _party(db: Session, user: User, order_id: int) -> tuple[Order, str]:
    """The order and which side of the conversation this user is. Anyone else gets a 404."""
    order = db.get(Order, order_id)
    if not order or order.mode != OrderMode.HOME:
        raise HTTPException(404, "Delivery order not found")
    if order.user_id == user.id:
        role = "RIDER"
    elif _delivery_driver_id(db, order) == user.id:
        role = "DRIVER"
    else:
        raise HTTPException(404, "Delivery order not found")
    if _delivery_driver_id(db, order) is None:
        raise HTTPException(409, "This opens once a delivery man is assigned to the order")
    return order, role


def _out(db: Session, m: OrderMessage) -> dict:
    u = db.get(User, m.sender_id)
    return {
        "id": m.id,
        "sender_role": m.sender_role,
        "sender_name": (u.full_name or u.username) if u else m.sender_role.title(),
        "body": m.body,
        "created_at": m.created_at,
    }


def list_messages(order_id: int, user: User, db: Session):
    order, _ = _party(db, user, order_id)
    rows = db.scalars(select(OrderMessage).where(OrderMessage.order_id == order.id).order_by(OrderMessage.id).limit(500))
    return {"closed": order.status in CLOSED or order.status == OrderStatus.COMPLETED, "messages": [_out(db, m) for m in rows]}


def send_message(order_id: int, body: str, user: User, db: Session):
    order, role = _party(db, user, order_id)
    if order.status in CLOSED or order.status == OrderStatus.COMPLETED:
        raise HTTPException(409, "This chat is closed because the order is finished")
    text = (body or "").strip()
    if not text or len(text) > 500:
        raise HTTPException(422, "Message must be 1 to 500 characters")
    m = OrderMessage(order_id=order.id, sender_id=user.id, sender_role=role, body=text)
    db.add(m)
    db.commit()
    db.refresh(m)
    return _out(db, m)


def request_locker_code(order_id: int, user: User, db: Session):
    """Delivery man asks the rider to show them the locker code."""
    order, role = _party(db, user, order_id)
    if role != "DRIVER":
        raise HTTPException(403, "Only the delivery man can request the locker code")
    if order.status in CLOSED or order.status == OrderStatus.COMPLETED:
        raise HTTPException(409, "This order is finished")
    if not order.locker_code_shared:
        order.locker_code_requested_at = now_utc()
        db.commit()
    return {"state": "SHARED" if order.locker_code_shared else "REQUESTED"}


def respond_locker_code(order_id: int, allow: bool, user: User, db: Session):
    """Rider allows (or declines) the delivery man seeing the locker code."""
    order, role = _party(db, user, order_id)
    if role != "RIDER":
        raise HTTPException(403, "Only the rider can allow this")
    if allow:
        order.locker_code_shared_at = now_utc()
        audit(db, user, "SHARE_LOCKER_CODE", "order", order.id, {"station_id": order.station_id})
    else:
        order.locker_code_requested_at = None
    db.commit()
    return {"state": "SHARED" if order.locker_code_shared else "NONE"}
