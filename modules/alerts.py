"""Business logic: alerts."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Optional

from constants.enums import AlertSeverity, AlertType
from integrations.hub import hub
from integrations.metrics import ALERTS
from models import Alert, User
from modules.audit import audit
from utils.access import scoped_station_id
from utils.dates import now_utc
from utils.serialization import to_wire

def raise_alert(
    db: Session,
    type_: AlertType,
    severity: AlertSeverity,
    message: str,
    *,
    station_id: Optional[str] = None,
    pack_id: Optional[str] = None,
) -> Optional[Alert]:
    existing = db.scalar(
        select(Alert).where(
            Alert.type == type_,
            Alert.resolved.is_(False),
            Alert.station_id == station_id,
            Alert.pack_id == pack_id,
        )
    )
    if existing:
        return None
    alert = Alert(type=type_, severity=severity, message=message, station_id=station_id, pack_id=pack_id)
    db.add(alert)
    db.flush()
    ALERTS.labels(type_.value, severity.value).inc()
    hub.publish(
        "alerts",
        to_wire(
            {
                "type": "ALERT",
                "id": alert.id,
                "alert_type": type_.value,
                "severity": severity.value,
                "message": message,
                "station_id": station_id,
                "pack_id": pack_id,
                "timestamp": now_utc().isoformat(),
            }
        ),
    )
    return alert


def resolve_alerts(
    db: Session,
    type_: AlertType,
    *,
    station_id: Optional[str] = None,
    pack_id: Optional[str] = None,
) -> int:
    n = 0
    for a in db.scalars(
        select(Alert).where(
            Alert.type == type_,
            Alert.resolved.is_(False),
            Alert.station_id == station_id,
            Alert.pack_id == pack_id,
        )
    ):
        a.resolved = True
        n += 1
    return n


def list_alerts(open_only: bool, actor: User, db: Session):
    q = select(Alert).order_by(Alert.id.desc())
    target_station = scoped_station_id(actor)
    if target_station:
        q = q.where(Alert.station_id == target_station)
    if open_only:
        q = q.where(Alert.resolved.is_(False))
    return list(db.scalars(q.limit(200)))


def ack_alert(alert_id: int, actor: User, db: Session):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    scoped_station_id(actor, a.station_id)
    a.acknowledged = True
    audit(db, actor, "ACK_ALERT", "alert", alert_id)
    db.commit()
    return a


def resolve_alert(alert_id: int, actor: User, db: Session):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    scoped_station_id(actor, a.station_id)
    a.resolved = a.acknowledged = True
    audit(db, actor, "RESOLVE_ALERT", "alert", alert_id)
    db.commit()
    return a
