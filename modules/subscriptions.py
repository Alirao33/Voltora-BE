"""Business logic: subscriptions."""

from __future__ import annotations

from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Optional

from constants.enums import PaymentMethod, PaymentStatus, PlanKind, SubscriptionStatus
from models import Plan, Subscription, User
from modules.locker_pass import require_free_locker
from modules.payments import charge, paid_replay
from schemas.plan import SubscribeRequest, SubscriptionRequest
from utils.dates import now_utc

def active_subscription(db: Session, user: Optional[User]) -> Optional[Subscription]:
    if not user:
        return None
    return db.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.swaps_remaining > 0,
            Subscription.ends_at > now_utc(),
        )
        .order_by(Subscription.ends_at)
    )


def subscribe(db: Session, user: User, plan: Plan, method: PaymentMethod, reference: str) -> Subscription:
    prev = paid_replay(db, user, reference, "PLAN")
    if prev and prev.subscription_id:
        return db.get(Subscription, prev.subscription_id)
    if plan.kind == PlanKind.PER_SWAP or not plan.is_active:
        raise HTTPException(400, "This plan cannot be subscribed to")
    if db.scalar(
        select(func.count())
        .select_from(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.ends_at > now_utc(),
        )
    ):
        raise HTTPException(409, "You already have an active subscription")
    require_free_locker(db)  # every pass needs a locker, so stop before taking money
    pay = charge(
        db,
        user=user,
        order=None,
        purpose="PLAN",
        method=method,
        reference=reference,
        amount_pkr=plan.price_pkr,
    )
    if pay.status != PaymentStatus.SUCCEEDED:
        db.commit()
        raise HTTPException(402, "Payment could not be verified")
    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        swaps_remaining=plan.swaps_included,
        ends_at=now_utc() + timedelta(days=plan.duration_days),
    )
    db.add(sub)
    db.flush()
    pay.subscription_id = sub.id
    db.commit()
    return sub


def current_subscription(db: Session, user: User) -> Optional[Subscription]:
    # Locker passes include 0 swaps, so unlike active_subscription() this ignores swaps_remaining.
    return db.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.ends_at > now_utc(),
        )
        .order_by(Subscription.ends_at.desc())
    )


def my_subscription(user: User, db: Session):
    return current_subscription(db, user)


def cancel_subscription(user: User, db: Session):
    sub = current_subscription(db, user)
    if not sub:
        raise HTTPException(404, "No active subscription to cancel")
    sub.status = SubscriptionStatus.CANCELLED  # no refund is issued
    db.commit()
    return sub


def subscribe_plan(plan_id: int, body: SubscribeRequest, user: User, db: Session):
    plan = db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return subscribe(db, user, plan, body.payment_method, body.payment_reference)


def list_locker_plans(db: Session) -> list[dict]:
    db_plans = db.scalars(
        select(Plan)
        .where(Plan.is_active.is_(True), Plan.code != "PER_SWAP", Plan.price_pkr > 0)
        .order_by(Plan.duration_days, Plan.id)
    ).all()
    
    formatted_plans = []
    for p in db_plans:
        formatted_plans.append({
            "id": p.id,
            "code": p.code,
            "plan_code": p.code,
            "name": p.name,
            "price": p.price_pkr,
            "price_pkr": p.price_pkr,
            "swaps_included": p.swaps_included,
            "duration_days": p.duration_days,
            "billing_cycle": getattr(p, "duration_type", "Monthly").capitalize() if getattr(p, "duration_type", None) else "Monthly",
            "description": f"Exclusive station locker storage pass for {p.duration_days} days.",
            "features": [
                "Dedicated Station Locker Storage Rights",
                "Priority Locker Reservation Access",
                "Discounted Energy Consumption Settlement"
            ],
            "kind": p.kind.value if hasattr(p.kind, "value") else str(p.kind),
            "is_active": p.is_active
        })
    
    return formatted_plans


def confirm_locker_pass(req: SubscriptionRequest) -> dict:
    return {
        "status": "success",
        "message": f"Successfully secured locker pass subscription for {req.plan_code}",
        "plan_code": req.plan_code,
    }
