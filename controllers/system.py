"""Thin controllers (HTTP in, module call, HTTP out): system."""

from __future__ import annotations

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session
from typing import Optional

from config.database import get_db
from modules import orders as orders_service
from modules import system as system_service

def health_live():
    return system_service.health_live()


def health_ready(db: Session = Depends(get_db)):
    return system_service.health_ready(db=db)


def prometheus_metrics():
    return system_service.prometheus_metrics()


async def payment_webhook(request: Request, x_signature: Optional[str] = Header(None), db: Session = Depends(get_db)):
    raw_body = await request.body()
    return orders_service.handle_webhook(db, raw_body, x_signature)
