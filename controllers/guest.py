"""Thin controllers (HTTP in, module call, HTTP out): guest."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from modules import guest as guest_service
from schemas.guest import GuestQuoteRequest, GuestSwapRequest

def public_stations(db: Session = Depends(get_db)):
    return guest_service.public_stations(db=db)


def public_station(station_id: str, db: Session = Depends(get_db)):
    return guest_service.public_station(station_id=station_id, db=db)


def public_plans(db: Session = Depends(get_db)):
    return guest_service.public_plans(db=db)


def guest_quote(body: GuestQuoteRequest, db: Session = Depends(get_db)):
    return guest_service.guest_quote(body=body, db=db)


def guest_pay_and_swap(body: GuestSwapRequest, db: Session = Depends(get_db)):
    return guest_service.guest_pay_and_swap(body=body, db=db)
