"""Thin controllers (HTTP in, module call, HTTP out): telemetry."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from modules import telemetry as telemetry_service
from schemas.telemetry import TelemetryIn

def ingest_telemetry(body: TelemetryIn, db: Session = Depends(get_db)):
    return telemetry_service.ingest_telemetry(body=body, db=db)


def get_all_latest_telemetry(db: Session = Depends(get_db)):
    """Return latest telemetry snapshot for all registered packs (used by live dashboard cards)."""
    return telemetry_service.get_all_latest_telemetry(db=db)


def latest_telemetry(pack_id: str, db: Session = Depends(get_db)):
    return telemetry_service.latest_telemetry(pack_id=pack_id, db=db)
