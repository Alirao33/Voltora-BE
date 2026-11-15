"""Business logic: payments."""

from __future__ import annotations

import hashlib
import hmac
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Optional

from config.settings import get_settings
from constants.enums import PaymentMethod, PaymentStatus
from integrations.payment_gateway import get_gateway, log_payments
from models import Order, Payment, User
from modules.audit import audit
from schemas.payment import RefundRequest

def _in_flight() -> HTTPException:
    # 503 (not 409) so the client keeps the reference; its retry gets the original result via paid_replay.
    return HTTPException(503, "This payment is still being processed. Retry in a moment.", headers={"Retry-After": "2"})


def charge(
    db: Session,
    *,
    user: Optional[User],
    order: Optional[Order],
    purpose: str,
    method: PaymentMethod,
    reference: str,
    amount_pkr: float,
) -> Payment:
    existing = db.scalar(select(Payment).where(Payment.reference == reference))
    if existing:
        if user and existing.user_id == user.id and existing.status != PaymentStatus.FAILED:
            raise _in_flight()  # same rider's attempt still running; a retry will replay it
        raise HTTPException(409, "This payment reference has already been used")
    p = Payment(
        user_id=user.id if user else None,
        order_id=order.id if order else None,
        purpose=purpose,
        method=method,
        reference=reference,
        amount_pkr=amount_pkr,
    )
    db.add(p)
    try:
        db.flush()  # claim the reference before the gateway is called, so a duplicate can't charge twice
    except IntegrityError:
        # A concurrent request with this reference won the race.
        db.rollback()
        raise _in_flight()
    res = get_gateway().charge(reference, method, amount_pkr)
    p.status = PaymentStatus.SUCCEEDED if res.ok else PaymentStatus.FAILED
    p.provider_ref = res.provider_ref
    db.flush()
    return p


def paid_replay(db: Session, user: Optional[User], reference: Optional[str], purpose: str) -> Optional[Payment]:
    """Safe retry: the same rider resending a reference that already succeeded gets that payment back
    instead of a 409, so a lost response never turns into a second charge."""
    if not (user and reference):
        return None
    p = db.scalar(select(Payment).where(Payment.reference == reference))
    if p and p.user_id == user.id and p.purpose == purpose and p.status != PaymentStatus.FAILED:
        return p
    return None


def refund(db: Session, payment: Payment, amount: Optional[float] = None, reason: str = "refund") -> Payment:
    if payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED):
        raise HTTPException(409, f"Payment is {payment.status.value}; nothing to refund")
    left = round(payment.amount_pkr - payment.refunded_pkr, 2)
    amount = left if amount is None else min(round(amount, 2), left)
    if amount <= 0:
        raise HTTPException(409, "Payment is already fully refunded")
    res = get_gateway().refund(payment.reference, amount)
    if not res.ok:
        raise HTTPException(502, f"Gateway refused refund: {res.message}")
    payment.refunded_pkr = round(payment.refunded_pkr + amount, 2)
    payment.status = (
        PaymentStatus.REFUNDED
        if payment.refunded_pkr >= payment.amount_pkr - 0.005
        else PaymentStatus.PARTIALLY_REFUNDED
    )
    payment.refund_reason = reason[:200]
    log_payments.info("Refunded PKR %.2f on %s (%s)", amount, payment.reference, reason)
    return payment


def verify_webhook(body: bytes, signature: Optional[str]) -> bool:
    expected = hmac.new(get_settings().payment_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def order_payment(db: Session, order_id: int) -> Optional[Payment]:
    return db.scalar(
        select(Payment)
        .where(Payment.order_id == order_id, Payment.purpose == "ORDER")
        .order_by(Payment.id.desc())
    )


def refund_payment(payment_id: int, body: RefundRequest, actor: User, db: Session):
    pay = db.get(Payment, payment_id)
    if not pay:
        raise HTTPException(404, "Payment not found")
    refund(db, pay, body.amount_pkr, body.reason)
    audit(db, actor, "REFUND", "payment", payment_id, {"amount_pkr": body.amount_pkr, "reason": body.reason})
    db.commit()
    return pay
