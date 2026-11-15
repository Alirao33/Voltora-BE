"""Pydantic schemas: alert."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel
from typing import Optional

from constants.enums import AlertSeverity, AlertType
from schemas.base import ORM

class AlertOut(BaseModel):
    model_config = ORM
    id: int
    type: AlertType
    severity: AlertSeverity
    message: str
    station_id: Optional[str] = None
    pack_id: Optional[str] = None
    acknowledged: bool
    resolved: bool
    created_at: datetime
