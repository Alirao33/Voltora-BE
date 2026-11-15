"""Thin controllers (HTTP in, module call, HTTP out): orders."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import delivery as delivery_service
from modules import orders as orders_service
from schemas.order import OrderReviewRequest

def admin_review_order(order_id: int, req: OrderReviewRequest, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return orders_service.admin_review_order(order_id=order_id, req=req, actor=actor, db=db)


def admin_list_orders(
    status: Optional[str] = None,
    station_id: Optional[str] = None,
    user_id: Optional[int] = None,
    limit: int = 100,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return orders_service.admin_list_orders(actor=actor, db=db, status=status, station_id=station_id, user_id=user_id, limit=limit)


def get_delivery_zones(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return delivery_service.get_delivery_zones(actor=actor, db=db)
