"""Business logic: rider home summary (held pack, unpaid pay-later bills, plan expiry alert)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from constants.enums import OrderStatus
from models import Battery, Order, Plan, User
from modules.subscriptions import current_subscription
from utils.dates import now_utc

# Pay Now alert opens when this many days (or fewer) are left: weekly 2, monthly 7, longer passes 30.
def _alert_days(duration_days: int) -> int:
    return 2 if duration_days <= 7 else 7 if duration_days <= 31 else 30


def rider_summary(user: User, db: Session) -> dict:
    last = db.scalar(
        select(Order)
        .where(Order.user_id == user.id, Order.status == OrderStatus.COMPLETED, Order.pack_out_id.is_not(None))
        .order_by(Order.id.desc())
    )
    pack = db.get(Battery, last.pack_out_id) if last else None
    unpaid = [
        {"order_id": o.id, "ref": o.order_id, "due_pkr": o.bill_due_pkr, "due_at": o.payment_due_at}
        for o in db.scalars(
            select(Order).where(Order.user_id == user.id, Order.status == OrderStatus.COMPLETED, Order.payment_due_at.is_not(None), Order.bill_paid_at.is_(None))
        )
        if (o.bill_due_pkr or 0) > 0
    ]
    sub = current_subscription(db, user)
    plan_alert = None
    if sub:
        plan = db.get(Plan, sub.plan_id)
        days_left = max(0, (sub.ends_at - now_utc()).total_seconds() / 86400)
        if days_left <= _alert_days(plan.duration_days):
            plan_alert = {"plan_id": plan.id, "plan_name": plan.name, "price_pkr": plan.price_pkr, "ends_at": sub.ends_at, "days_left": round(days_left, 1)}
    return {
        "held_pack": {"id": pack.id, "soc": pack.soc, "soh": pack.soh, "state": pack.state.value, "temperature_c": pack.temperature_c} if pack else None,
        "unpaid_bills": unpaid,
        "plan_alert": plan_alert,
    }
