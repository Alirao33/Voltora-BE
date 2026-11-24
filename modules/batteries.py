"""Business logic: batteries."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Optional

from config.settings import get_settings
from constants.enums import AlertSeverity, AlertType, BatteryState, HealthStatus
from integrations.devices import send_command
from datetime import timedelta
from models import Battery, Station, TelemetryReading, User
from modules.alerts import raise_alert, resolve_alerts
from modules.audit import audit
from modules.battery_health import health_score
from schemas.battery import BatteryCreate, BatteryStateUpdate
from utils.access import scoped_station_id
from utils.dates import now_utc

def lock_pack(db: Session, b: Battery, reason: str) -> None:
    """Over-temperature lockout: slot is locked via MQTT/device command and pack cannot be issued."""
    b.state = BatteryState.LOCKED
    raise_alert(
        db,
        AlertType.LOCKOUT,
        AlertSeverity.CRITICAL,
        f"Pack {b.id} locked out: {reason}",
        station_id=b.station_id,
        pack_id=b.id,
    )
    send_command(b.station_id, b.locker_id, {"command": "LOCK_SLOT", "pack_id": b.id})


def retire_pack(db: Session, b: Battery) -> None:
    """Health < 70 -> retire pack to second-life stationary storage."""
    b.state = BatteryState.RETIRED
    b.reserved_order_id = None
    raise_alert(
        db,
        AlertType.PACK_RETIRED,
        AlertSeverity.WARNING,
        f"Pack {b.id} retired to second-life storage (health {b.health_score})",
        station_id=b.station_id,
        pack_id=b.id,
    )


def pick_pack(db: Session, station_id: str) -> Optional[Battery]:
    """Select best issuable pack: CHARGED, SoC >= min_issue_soc, safe temp, and healthy."""
    s = get_settings()
    q = (
        select(Battery)
        .where(
            Battery.station_id == station_id,
            Battery.state == BatteryState.CHARGED,
            Battery.soc >= s.min_issue_soc,
        )
        .order_by(Battery.health_score.desc(), Battery.soc.desc(), Battery.id)
        .with_for_update(skip_locked=True)
    )
    for b in list(db.scalars(q)):
        if b.temperature_c > s.lockout_c:
            lock_pack(db, b, f"{b.temperature_c} C")
            continue
        if b.health_score < s.retire_health:
            retire_pack(db, b)
            continue
        return b
    return None


def apply_health(b: Battery) -> None:
    cfg = get_settings()
    r = health_score(
        soh=b.soh,
        temperature_c=b.temperature_c,
        cycles=b.cycle_count,
        faults=b.fault_count,
        operating_hours=b.operating_hours,
        chemistry=b.chemistry,
        warning=cfg.warning_health,
        retire=cfg.retire_health,
    )
    b.health_score, b.health_status = r.score, r.status


def second_life_packs(db: Session) -> list[Battery]:
    return list(db.scalars(select(Battery).where(Battery.state == BatteryState.RETIRED)))


def evaluate_pack_health(db: Session, b: Battery) -> None:
    """Trigger maintenance warnings or retirement after a health score recalculation."""
    if b.state == BatteryState.RETIRED:
        return
    if b.health_status == HealthStatus.RETIRE and b.state not in (BatteryState.IN_USE, BatteryState.RESERVED):
        retire_pack(db, b)
    elif b.health_status == HealthStatus.WARNING:
        raise_alert(
            db,
            AlertType.WEAK_PACK,
            AlertSeverity.WARNING,
            f"Pack {b.id} needs maintenance (health {b.health_score})",
            station_id=b.station_id,
            pack_id=b.id,
        )
    elif b.health_status == HealthStatus.NORMAL:
        resolve_alerts(db, AlertType.WEAK_PACK, station_id=b.station_id, pack_id=b.id)


def list_batteries(station_id: Optional[str], state: Optional[BatteryState], actor: User, db: Session):
    station_id = scoped_station_id(actor, station_id)
    q = select(Battery).order_by(Battery.id)
    if station_id:
        q = q.where(Battery.station_id == station_id)
    if state:
        q = q.where(Battery.state == state)
    return list(db.scalars(q))


def create_battery(body: BatteryCreate, actor: User, db: Session):
    target_station = scoped_station_id(actor, body.station_id)
    if target_station:
        body.station_id = target_station
    if db.get(Battery, body.id):
        raise HTTPException(409, f"Pack {body.id} already exists")
    if body.station_id and not db.get(Station, body.station_id):
        raise HTTPException(404, "Station not found")
    b = Battery(
        id=body.id,
        chemistry=body.chemistry,
        rated_capacity_ah=body.rated_capacity_ah,
        nominal_voltage=body.nominal_voltage,
        full_charge_capacity_ah=body.rated_capacity_ah,
        remaining_ah=body.rated_capacity_ah,
        voltage=body.nominal_voltage,
        station_id=body.station_id,
        locker_id=body.locker_id,
        state=body.state,
    )
    db.add(b)
    audit(db, actor, "REGISTER_PACK", "battery", body.id)
    db.commit()
    return b


def update_battery(pack_id: str, body: BatteryStateUpdate, actor: User, db: Session):
    b = db.get(Battery, pack_id)
    if not b:
        raise HTTPException(404, "Pack not found")
    scoped_station_id(actor, b.station_id)
    if body.state == BatteryState.CHARGED and b.temperature_c > get_settings().lockout_c:
        raise HTTPException(409, f"Pack is at {b.temperature_c} C, cool down before unlocking")
    b.state = body.state
    if body.station_id is not None:
        target_station = scoped_station_id(actor, body.station_id)
        b.station_id = target_station or (body.station_id or None)
    if body.state == BatteryState.CHARGED:
        resolve_alerts(db, AlertType.LOCKOUT, pack_id=b.id)
    audit(db, actor, "SET_PACK_STATE", "battery", pack_id, {"state": body.state.value})
    db.commit()
    return b


def get_second_life_batteries(actor: User, db: Session):
    packs = second_life_packs(db)
    target_station = scoped_station_id(actor)
    if target_station:
        return [p for p in packs if p.station_id == target_station]
    return packs


def get_battery(pack_id: str, actor: User, db: Session):
    b = db.get(Battery, pack_id)
    if not b:
        raise HTTPException(404, "Pack not found")
    scoped_station_id(actor, b.station_id)
    return b


def telemetry_history(pack_id: str, hours: int, limit: int, actor: User, db: Session):
    """Readings for one pack, oldest first, newest `limit` within the last `hours`."""
    get_battery(pack_id, actor, db)
    rows = db.scalars(
        select(TelemetryReading)
        .where(TelemetryReading.pack_id == pack_id, TelemetryReading.ts >= now_utc() - timedelta(hours=hours))
        .order_by(TelemetryReading.ts.desc())
        .limit(limit)
    )
    return list(reversed(list(rows)))
