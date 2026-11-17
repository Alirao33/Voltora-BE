"""Business logic: orders."""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Any, Optional

from config.settings import get_settings
from constants.enums import (
    ActionType,
    AlertSeverity,
    AlertType,
    BatteryState,
    BookingStatus,
    DoorLockState,
    OrderMode,
    OrderStatus,
    PaymentStatus,
    TaskKind,
    TaskStatus,
)
from constants.orders import DEFAULT_CAPACITY_FRACTION, OPEN_ORDER, PAY_LATER_GRACE_DAYS
from integrations.metrics import ORDERS
from models import (
    Battery,
    Booking,
    Locker,
    Order,
    Payment,
    Station,
    Subscription,
    User,
    Van,
    VanTask,
)
from modules.alerts import raise_alert
from modules.audit import audit
from modules.batteries import apply_health, evaluate_pack_health, pick_pack
from modules.bookings import place_booking, release_booking
from modules.profit import record_profit
from modules.payments import charge, order_payment, paid_replay, verify_webhook
from modules.payments import refund as refund_payment  # release_order's `refund` flag would shadow it
from modules.pricing import calculate_price, demand_factor, get_pricing_config
from modules.stations import check_stock_alert, resolve_station, stock_ratio
from modules.subscriptions import active_subscription, current_subscription
from schemas.order import (
    CompleteOrderRequest,
    OrderCreate,
    OrderReviewRequest,
    PayBillRequest,
    RateRequest,
    UnlockRequest,
)
from schemas.payment import PaymentWebhook
from utils.access import scoped_station_id
from utils.dates import now_utc
from utils.geo import haversine_km
from utils.numbers import money

log_swap = logging.getLogger("voltora.swap")


def new_order_code() -> str:
    return f"{secrets.randbelow(10000):04d}"


def unique_pickup_code(db: Session) -> str:
    """A locker code no other open order is using, so it differs every time."""
    for _ in range(50):
        code = new_order_code()
        if not db.scalar(select(Order.id).where(Order.pickup_code == code, Order.status.in_(OPEN_ORDER))):
            return code
    return new_order_code()


def new_guest_ref() -> str:
    return f"GUEST-{secrets.token_hex(3).upper()}"


def release_order(
    db: Session, order: Order, new_status: OrderStatus, *, refund: bool = True, reason: str = ""
) -> None:
    """Release reserved pack, locker hold, subscription credit, and pending delivery tasks."""
    if order.pack_out_id:
        pack = db.get(Battery, order.pack_out_id)
        if pack and pack.state == BatteryState.RESERVED:
            pack.state, pack.reserved_order_id = BatteryState.CHARGED, None
    if order.booking_id:
        b = db.get(Booking, order.booking_id)
        if b:
            release_booking(
                db,
                b,
                BookingStatus.CANCELLED if new_status == OrderStatus.CANCELLED else BookingStatus.EXPIRED,
            )
    if order.subscription_id and order.status in OPEN_ORDER:
        sub = db.get(Subscription, order.subscription_id)
        if sub:
            sub.swaps_remaining += 1
    for t in db.scalars(
        select(VanTask).where(VanTask.order_id == order.id, VanTask.status == TaskStatus.PENDING)
    ):
        t.status = TaskStatus.CANCELLED
    if refund:
        pay = order_payment(db, order.id)
        if pay and pay.status in (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED):
            refund_payment(db, pay, None, reason or f"order {new_status.value.lower()}")
    order.status = new_status
    ORDERS.labels(new_status.value).inc()


def create_delivery_task(db: Session, order: Order, st: Station) -> VanTask:
    vans = list(db.scalars(select(Van).where(Van.is_active.is_(True), Van.latitude.is_not(None))))
    van = min(
        vans,
        key=lambda v: haversine_km(v.latitude, v.longitude, order.delivery_lat, order.delivery_lon),
        default=None,
    )
    t = VanTask(
        van_id=van.id if van else None,
        kind=TaskKind.DELIVERY,
        station_id=st.id,
        order_id=order.id,
        latitude=order.delivery_lat,
        longitude=order.delivery_lon,
        address=order.delivery_address,
        drop_count=1,
        pickup_count=1,
    )
    db.add(t)
    db.flush()
    return t


def create_order(
    db: Session,
    *,
    user: Optional[User],
    req: OrderCreate,
    guest_name: Optional[str] = None,
    guest_phone: Optional[str] = None,
    capacity_used_ah: Optional[float] = None,
) -> Order:
    s = get_settings()
    # Before the open-order check: a retry of a paid attempt must not trip over its own order.
    prev = paid_replay(db, user, req.payment_reference, "ORDER")
    if prev and prev.order_id:
        return db.get(Order, prev.order_id)
    if user and db.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.user_id == user.id, Order.status.in_((*OPEN_ORDER, OrderStatus.AWAITING_APPROVAL)))
    ):
        raise HTTPException(409, "You already have an open swap order")

    st = resolve_station(db, req.station_id, req.latitude, req.longitude)
    if not st.is_active or st.is_depot:
        raise HTTPException(409, "Station is not open to riders")

    pack = pick_pack(db, st.id)
    if pack is None:
        raise_alert(
            db,
            AlertType.STOCK_OUT,
            AlertSeverity.CRITICAL,
            f"{st.name}: swap refused, no charged pack",
            station_id=st.id,
        )
        db.commit()
        raise HTTPException(409, "No charged pack available at this station")

    cfg = get_pricing_config(db)
    now = now_utc()
    delivery = req.mode == OrderMode.HOME
    ah = capacity_used_ah or req.capacity_used_ah or round(
        pack.full_charge_capacity_ah * DEFAULT_CAPACITY_FRACTION, 2
    )
    df = demand_factor(now, stock_ratio(db, st), cfg)
    price = calculate_price(
        voltage=pack.nominal_voltage,
        capacity_used_ah=ah,
        chemistry=pack.chemistry,
        delivery=delivery,
        delivery_km=(
            haversine_km(st.latitude, st.longitude, req.delivery_lat, req.delivery_lon)
            if delivery and req.delivery_lat is not None and req.delivery_lon is not None
            else None
        ),
        demand=df,
        cfg=cfg,
        soc_used_pct=ah / pack.full_charge_capacity_ah * 100 if pack.full_charge_capacity_ah else None,
        soh_pct=pack.soh,
        pack=pack,
    )
    due_at = None
    if req.pay_later:
        # Pay-later is a locker-pass perk: bill falls due PAY_LATER_GRACE_DAYS after the pass ends.
        locker_sub = current_subscription(db, user) if user else None
        if not locker_sub:
            raise HTTPException(403, "Pay later is only available to subscribed riders")
        due_at = locker_sub.ends_at + timedelta(days=PAY_LATER_GRACE_DAYS)
        sub, charge_amount = None, 0.0
    else:
        sub = active_subscription(db, user) if (user and req.use_subscription) else None
        charge_amount = price.delivery_component_pkr if sub else price.total_pkr

    order = Order(
        user_id=user.id if user else None,
        guest_ref=None if user else new_guest_ref(),
        guest_name=guest_name,
        guest_phone=guest_phone,
        station_id=st.id,
        mode=req.mode,
        status=OrderStatus.PENDING_PAYMENT,
        subscription_id=sub.id if sub else None,
        plan_id=sub.plan_id if sub else None,
        pack_out_id=pack.id,
        price=price.model_dump(),
        total_pkr=price.total_pkr,
        charged_pkr=charge_amount,
        payment_due_at=due_at,
        pickup_code=unique_pickup_code(db),
        delivery_address=req.delivery_address,
        delivery_lat=req.delivery_lat,
        delivery_lon=req.delivery_lon,
        delivery_otp=new_order_code() if delivery else None,
        hold_expires_at=None if delivery else now + timedelta(minutes=s.booking_grace_minutes),
    )
    db.add(order)
    db.flush()
    pack.state, pack.reserved_order_id = BatteryState.RESERVED, order.id
    order.slot_id = pack.locker_id

    if req.mode == OrderMode.LOCKER:
        bk = place_booking(
            db,
            user=user,
            guest_ref=order.guest_ref,
            station_id=st.id,
            locker_id=req.locker_id,
            action=ActionType.SWAP_DROP_AND_PICK if user else ActionType.GUEST_PAY_SWAP,
            scheduled_time=req.scheduled_time,
            status=BookingStatus.PENDING,
            order_id=order.id,
            code=order.pickup_code,
        )
        order.booking_id, order.slot_id = bk.id, bk.locker_id
        order.hold_expires_at = bk.hold_expires_at

    if charge_amount > 0:
        if not (req.payment_method and req.payment_reference):
            raise HTTPException(
                402,
                f"Pay first: PKR {charge_amount:.2f} due (payment_method and payment_reference required)",
            )
        pay = charge(
            db,
            user=user,
            order=order,
            purpose="ORDER",
            method=req.payment_method,
            reference=req.payment_reference,
            amount_pkr=charge_amount,
        )
        if pay.status != PaymentStatus.SUCCEEDED:
            release_order(db, order, OrderStatus.FAILED, refund=False)
            db.commit()
            raise HTTPException(402, "Payment could not be verified")

    if sub:
        sub.swaps_remaining -= 1
    # Home delivery waits for admin approval; the van task is created when the admin approves.
    order.status = OrderStatus.AWAITING_APPROVAL if delivery else OrderStatus.PAID
    order.paid_at = now
    if order.booking_id:
        bk = db.get(Booking, order.booking_id)
        if bk:
            bk.status = BookingStatus.CONFIRMED
    db.commit()
    ORDERS.labels(order.status.value).inc()
    return order


def unlock_order(db: Session, order: Order, code: str) -> Order:
    if order.status != OrderStatus.PAID:
        raise HTTPException(409, f"Order is {order.status.value}")
    if order.mode == OrderMode.HOME:
        raise HTTPException(400, "Home-delivery orders are handed over by the van driver")
    if not order.pickup_code or not secrets.compare_digest(code.encode(), order.pickup_code.encode()):
        raise HTTPException(403, "Invalid pickup code")
    slot = db.get(Locker, order.slot_id) if order.slot_id else None
    if slot:
        slot.door_lock_state = DoorLockState.UNLOCKED
    order.status = OrderStatus.IN_PROGRESS
    db.commit()
    return order


def complete_order(db: Session, order: Order, returned_pack_id: Optional[str] = None) -> Order:
    """Finalize swap: release fresh pack, inspect returned pack, settle price delta, and award points."""
    if order.status not in OPEN_ORDER:
        raise HTTPException(409, f"Order is {order.status.value}")
    cfg = get_settings()
    out = db.get(Battery, order.pack_out_id) if order.pack_out_id else None
    if out:
        out.state, out.station_id, out.locker_id, out.van_id, out.reserved_order_id = (
            BatteryState.IN_USE,
            None,
            None,
            None,
            None,
        )

    if returned_pack_id:
        old = db.get(Battery, returned_pack_id)
        if not old:
            raise HTTPException(404, f"Returned pack {returned_pack_id} not found")
        order.pack_in_id = old.id
        old.station_id = order.station_id
        old.state = BatteryState.DEPLETED
        old.van_id = None
        apply_health(old)
        evaluate_pack_health(db, old)
        ah_used = max(0.0, old.full_charge_capacity_ah - old.remaining_ah)
        if ah_used > 0:
            price_cfg = get_pricing_config(db)
            actual = calculate_price(
                voltage=old.nominal_voltage,
                capacity_used_ah=ah_used,
                chemistry=old.chemistry,
                delivery=order.mode == OrderMode.HOME,
                delivery_km=order.price.get("delivery_km"),
                demand=order.price.get("demand_factor", 1.0),
                cfg=price_cfg,
                returned_temp_c=old.temperature_c,
                soc_used_pct=ah_used / old.full_charge_capacity_ah * 100 if old.full_charge_capacity_ah else None,
                soh_pct=old.soh,
                pack=old,
            )
            total = actual.total_pkr
            out = db.get(Battery, order.pack_out_id) if order.pack_out_id else None
            if out is not None and old.soh >= out.soh and price_cfg.same_soh_discount_pct > 0:
                # Returned in the same SoH (or better): discount on the whole bill of this swap.
                disc = money(total * price_cfg.same_soh_discount_pct)
                total = money(total - disc)
                order.price = {**order.price, "same_soh_discount_pkr": disc}
            order.actual_total_pkr = total
            record_profit(
                db, order, units_kwh=actual.energy_kwh, soc_charge=actual.soc_charge_pkr, soh_charge=actual.soh_charge_pkr,
                before_discount=actual.total_pkr, after_discount=total,
            )
            if order.subscription_id is None and order.payment_due_at is None:
                order.settlement_delta_pkr = money(total - order.charged_pkr)
                if order.settlement_delta_pkr < -1.0:
                    pay = order_payment(db, order.id)
                    if pay and pay.status == PaymentStatus.SUCCEEDED:
                        refund_payment(
                            db, pay, -order.settlement_delta_pkr, "settlement on actual energy used"
                        )

    # No usable return reading (pack came back full or not scanned): log profit from the quoted price instead.
    q = order.price or {}
    record_profit(
        db, order, units_kwh=q.get("energy_kwh") or 0.0, soc_charge=q.get("soc_charge_pkr") or 0.0, soh_charge=q.get("soh_charge_pkr") or 0.0,
        before_discount=order.total_pkr, after_discount=order.total_pkr,
    )

    if order.booking_id:
        b = db.get(Booking, order.booking_id)
        if b:
            release_booking(db, b, BookingStatus.COMPLETED)

    order.status, order.completed_at = OrderStatus.COMPLETED, now_utc()
    order.receipt_no = f"RCP-{order.id:06d}"
    if order.user_id:
        u = db.get(User, order.user_id)
        if u:
            u.points += cfg.rider_points_per_swap
    st = db.get(Station, order.station_id)
    db.flush()
    check_stock_alert(db, st)
    db.commit()
    ORDERS.labels("COMPLETED").inc()
    return order


def cancel_order(db: Session, order: Order, reason: str = "cancelled by rider") -> Order:
    if order.status not in (OrderStatus.PAID, OrderStatus.AWAITING_APPROVAL):
        raise HTTPException(
            409,
            f"Only paid, not-yet-collected orders can be cancelled (order is {order.status.value})",
        )
    release_order(db, order, OrderStatus.CANCELLED, refund=True, reason=reason)
    db.commit()
    return order


def expire_stale_orders(db: Session) -> int:
    now = now_utc()
    n = 0
    for o in db.scalars(
        select(Order).where(
            Order.status == OrderStatus.PAID,
            Order.hold_expires_at.is_not(None),
            Order.hold_expires_at < now,
        )
    ):
        release_order(db, o, OrderStatus.EXPIRED, refund=True, reason="not collected in time")
        n += 1
    if n:
        db.commit()
    return n


def receipt(db: Session, order: Order) -> dict[str, Any]:
    pay = order_payment(db, order.id)
    return {
        "receipt_no": order.receipt_no,
        "order_id": order.order_id,
        "date": order.completed_at or order.created_at,
        "station_id": order.station_id,
        "mode": order.mode,
        "pack_issued": order.pack_out_id,
        "pack_returned": order.pack_in_id,
        "breakdown": order.price,
        "total_pkr": order.total_pkr,
        "charged_pkr": order.charged_pkr,
        "actual_total_pkr": order.actual_total_pkr,
        "settlement_delta_pkr": order.settlement_delta_pkr,
        "covered_by_subscription": order.subscription_id is not None,
        "payment": {
            "method": pay.method,
            "reference": pay.reference,
            "status": pay.status,
            "refunded_pkr": pay.refunded_pkr,
        }
        if pay
        else None,
    }


def pay_order_bill(order_id: int, body: PayBillRequest, user: User, db: Session) -> Order:
    order = _own_order(db, user, order_id)
    prev = paid_replay(db, user, body.payment_reference, "BILL")
    if prev and prev.order_id == order.id:
        return order
    if order.payment_due_at is None:
        raise HTTPException(400, "This order has no pay-later bill")
    if order.bill_paid_at:
        raise HTTPException(409, "This bill is already paid")
    amount = order.bill_due_pkr
    if amount is None:
        raise HTTPException(409, f"Order is {order.status.value}; nothing to pay")
    penalty = order.bill_penalty_pkr
    pay = charge(
        db,
        user=user,
        order=order,
        purpose="BILL",
        method=body.payment_method,
        reference=body.payment_reference,
        amount_pkr=amount,
    )
    if pay.status != PaymentStatus.SUCCEEDED:
        db.commit()
        raise HTTPException(402, "Payment could not be verified")
    order.bill_paid_at, order.penalty_pkr, order.charged_pkr = now_utc(), penalty, amount
    db.commit()
    return order


def _own_order(db: Session, user: User, order_id: int) -> Order:
    o = db.get(Order, order_id)
    if not o or (o.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Order not found")
    return o


def create_rider_order(body: OrderCreate, user: User, db: Session):
    return create_order(db, user=user, req=body)


def list_orders(user: User, db: Session):
    return list(db.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(100)))


def admin_list_orders(
    actor: User,
    db: Session,
    status: Optional[str] = None,
    station_id: Optional[str] = None,
    user_id: Optional[int] = None,
    limit: int = 100,
):
    q = select(Order).order_by(Order.id.desc()).limit(min(max(limit, 1), 500))
    sid = scoped_station_id(actor, station_id)  # station admins only ever see their own station
    if sid:
        q = q.where(Order.station_id == sid)
    if status:
        try:
            q = q.where(Order.status == OrderStatus(status.upper()))
        except ValueError:
            raise HTTPException(422, f"Unknown order status {status}")
    if user_id:
        q = q.where(Order.user_id == user_id)
    return list(db.scalars(q))


def get_order(order_id: int, user: User, db: Session):
    return _own_order(db, user, order_id)


def unlock_rider_order(order_id: int, body: UnlockRequest, user: User, db: Session):
    return unlock_order(db, _own_order(db, user, order_id), body.pickup_code)


def complete_rider_order(order_id: int, body: CompleteOrderRequest, user: User, db: Session):
    return complete_order(db, _own_order(db, user, order_id), body.returned_pack_id)


def cancel_rider_order(order_id: int, user: User, db: Session):
    return cancel_order(db, _own_order(db, user, order_id))


def rate_order(order_id: int, body: RateRequest, user: User, db: Session):
    o = _own_order(db, user, order_id)
    if o.status != OrderStatus.COMPLETED:
        raise HTTPException(409, "Only completed orders can be rated")
    o.rating, o.review = body.rating, body.review
    db.commit()
    return o


def get_receipt(order_id: int, user: User, db: Session):
    o = _own_order(db, user, order_id)
    if o.status != OrderStatus.COMPLETED:
        raise HTTPException(409, "Receipt is available once the order is completed")
    return receipt(db, o)


def admin_review_order(order_id: int, req: OrderReviewRequest, actor: User, db: Session):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    scoped_station_id(actor, order.station_id)
    action = req.action.upper()
    if action in ("CANCEL", "REJECT") and order.status in (OrderStatus.PAID, OrderStatus.AWAITING_APPROVAL):
        release_order(db, order, OrderStatus.CANCELLED, refund=True, reason=req.reason or "admin review")
    elif action == "APPROVE" and order.status == OrderStatus.AWAITING_APPROVAL:
        order.status = OrderStatus.PAID
        create_delivery_task(db, order, db.get(Station, order.station_id))  # dispatches the nearest van
    elif action == "APPROVE" and order.status == OrderStatus.PENDING_PAYMENT:
        order.status = OrderStatus.PAID
        order.paid_at = now_utc()
    audit(db, actor, "REVIEW_ORDER", "order", order_id, {"action": action, "reason": req.reason})
    db.commit()
    return {"order_id": order.order_id, "action": action, "status": order.status.value, "reason": req.reason}


def handle_webhook(db: Session, raw_body: bytes, x_signature: Optional[str]):
    if not verify_webhook(raw_body, x_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")

    payload = PaymentWebhook.model_validate_json(raw_body)
    pay = db.scalar(select(Payment).where(Payment.reference == payload.reference))
    if not pay:
        raise HTTPException(404, "Payment reference not found")

    if payload.provider_ref:
        pay.provider_ref = payload.provider_ref
    outcome = payload.status.upper()
    if outcome == "SUCCEEDED" and pay.status == PaymentStatus.PENDING:
        pay.status = PaymentStatus.SUCCEEDED
        if pay.order_id:
            order = db.get(Order, pay.order_id)
            if order and order.status == OrderStatus.PENDING_PAYMENT:
                order.status = OrderStatus.PAID
                order.paid_at = now_utc()
    elif outcome == "FAILED" and pay.status == PaymentStatus.PENDING:
        pay.status = PaymentStatus.FAILED
        if pay.order_id:
            order = db.get(Order, pay.order_id)
            if order and order.status == OrderStatus.PENDING_PAYMENT:
                release_order(db, order, OrderStatus.FAILED, refund=False)

    db.commit()
    return {"received": True, "reference": pay.reference, "status": pay.status.value}
