"""Thin controllers (HTTP in, module call, HTTP out): guest."""

from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from config.database import get_db
from modules import guest as guest_service
from schemas.guest import GuestQuoteRequest, GuestSwapRequest

def public_stations(
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    radius_km: Optional[float] = Query(None, gt=0, le=500),
    db: Session = Depends(get_db),
):
    return guest_service.public_stations(db=db, lat=lat, lon=lon, radius_km=radius_km)


def public_station(station_id: str, db: Session = Depends(get_db)):
    return guest_service.public_station(station_id=station_id, db=db)


def public_plans(db: Session = Depends(get_db)):
    return guest_service.public_plans(db=db)


def guest_quote(body: GuestQuoteRequest, db: Session = Depends(get_db)):
    return guest_service.guest_quote(body=body, db=db)


def guest_pay_and_swap(body: GuestSwapRequest, db: Session = Depends(get_db)):
    return guest_service.guest_pay_and_swap(body=body, db=db)
