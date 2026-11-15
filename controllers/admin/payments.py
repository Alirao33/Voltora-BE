"""Thin controllers (HTTP in, module call, HTTP out): payments."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import payments as payments_service
from schemas.payment import RefundRequest

def refund_payment(payment_id: int, body: RefundRequest, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return payments_service.refund_payment(payment_id=payment_id, body=body, actor=actor, db=db)
