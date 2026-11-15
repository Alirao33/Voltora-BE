"""Thin controllers (HTTP in, module call, HTTP out): sites."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import sites as sites_service
from schemas.station import StationCreate, StationCreateRequest, StationUpdate

def list_sites(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return sites_service.list_sites(actor=actor, db=db)


def create_site(body: StationCreate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return sites_service.create_site(body=body, actor=actor, db=db)


def create_station_portal(req: StationCreateRequest, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a station from the Admin portal and auto-provision its locker slots."""
    return sites_service.create_station_portal(req=req, actor=actor, db=db)


def update_site(station_id: str, body: StationUpdate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return sites_service.update_site(station_id=station_id, body=body, actor=actor, db=db)
