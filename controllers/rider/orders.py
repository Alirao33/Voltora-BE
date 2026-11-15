"""Thin controllers (HTTP in, module call, HTTP out): orders."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import orders as orders_service
from schemas.order import CompleteOrderRequest, OrderCreate, PayBillRequest, RateRequest, UnlockRequest

def create_rider_order(body: OrderCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.create_rider_order(body=body, user=user, db=db)


def list_orders(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.list_orders(user=user, db=db)


def get_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.get_order(order_id=order_id, user=user, db=db)


def unlock_rider_order(order_id: int, body: UnlockRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.unlock_rider_order(order_id=order_id, body=body, user=user, db=db)


def complete_rider_order(order_id: int, body: CompleteOrderRequest = CompleteOrderRequest(), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.complete_rider_order(order_id=order_id, body=body, user=user, db=db)


def cancel_rider_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.cancel_rider_order(order_id=order_id, user=user, db=db)


def rate_order(order_id: int, body: RateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.rate_order(order_id=order_id, body=body, user=user, db=db)


def pay_order_bill(order_id: int, body: PayBillRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.pay_order_bill(order_id=order_id, body=body, user=user, db=db)


def get_receipt(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.get_receipt(order_id=order_id, user=user, db=db)
