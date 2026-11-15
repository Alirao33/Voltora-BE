"""Business logic: telemetry."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Any, Optional

from config.settings import get_settings
from constants.enums import AlertSeverity, AlertType, BatteryState
from integrations.devices import send_command
from integrations.hub import hub
from integrations.metrics import TELEMETRY_MSGS
from models import Battery, Station, TelemetryReading
from modules.alerts import raise_alert, resolve_alerts
from modules.batteries import apply_health, evaluate_pack_health, lock_pack
from modules.battery_health import soc_pct, soh_pct
from modules.stations import check_stock_alert
from schemas.telemetry import TelemetryIn
from utils.dates import ensure_aware, now_utc
from utils.serialization import to_wire

log_telemetry = logging.getLogger("voltora.telemetry")


@dataclass
class IngestResult:
    pack_id: str
    duplicate: bool = False
    trip_relay: bool = False
    lockout: bool = False
    health_score: Optional[float] = None


def telemetry_frame(b: Battery, ts=None) -> dict[str, Any]:
    return {
        "type": "TELEMETRY",
        "pack_id": b.id,
        "voltage": b.voltage,
        "current_a": b.current_a,
        "soc": b.soc,
        "soh": b.soh,
        "temp_c": b.temperature_c,
        "cycle_count": b.cycle_count,
        "composite_health_score": b.health_score,
        "health_status": b.health_status.value,
        "state": b.state.value,
        "current_amperage": b.current_a,
        "temperature_c": b.temperature_c,
        "timestamp": (ts or now_utc()).isoformat(),
    }


def emergency_frame(b: Battery, command: str, alert: str, threshold: float) -> dict[str, Any]:
    return {
        "type": "EMERGENCY",
        "command": command,
        "alert": alert,
        "pack_id": b.id,
        "temp_c": b.temperature_c,
        "temperature_c": b.temperature_c,
        "threshold_c": threshold,
        "timestamp": now_utc().isoformat(),
    }


def frames_for(b: Battery) -> list[dict[str, Any]]:
    s = get_settings()
    frames = [telemetry_frame(b)]
    if b.temperature_c > s.lockout_c:
        frames.append(emergency_frame(b, "LOCK_SLOT", "OVERHEAT_LOCKOUT", s.lockout_c))
    elif b.temperature_c > s.overheat_trip_c:
        frames.append(emergency_frame(b, "TRIP_RELAY", "OVERHEAT_CRITICAL", s.overheat_trip_c))
    return frames


def merge_partial(db: Session, pack_id: str, raw: dict[str, Any]) -> TelemetryIn:
    """Fill gaps in partial WebSocket device messages from the pack's last stored state."""
    b = db.get(Battery, pack_id)
    base: dict[str, Any] = {}
    if b:
        base = {"voltage": b.voltage, "current_a": b.current_a, "temperature_c": b.temperature_c}
    raw = dict(raw)
    for old, new in (("current_amperage", "current_a"), ("temp_c", "temperature_c")):
        if old in raw:
            raw.setdefault(new, raw.pop(old))
    return TelemetryIn.model_validate({**base, **raw, "pack_id": pack_id})


def ingest_reading(db: Session, r: TelemetryIn) -> IngestResult:
    s = get_settings()
    now = now_utc()
    ts = ensure_aware(r.ts) if r.ts else now
    if ts > now + timedelta(minutes=5):
        TELEMETRY_MSGS.labels("rejected").inc()
        raise ValueError("timestamp is in the future")

    b = db.get(Battery, r.pack_id, with_for_update=True)
    if b is None:
        if not s.auto_register_packs:
            TELEMETRY_MSGS.labels("rejected").inc()
            raise LookupError(f"Unknown pack {r.pack_id}")
        b = Battery(id=r.pack_id, station_id=r.station_id, locker_id=r.slot_id)
        db.add(b)
        db.flush()

    if db.get(TelemetryReading, (r.pack_id, ts)) is not None:
        TELEMETRY_MSGS.labels("duplicate").inc()
        return IngestResult(r.pack_id, duplicate=True)

    b.voltage, b.current_a, b.temperature_c = r.voltage, r.current_a, r.temperature_c
    if r.rated_ah:
        b.rated_capacity_ah = r.rated_ah
    if r.full_charge_ah:
        b.full_charge_capacity_ah = r.full_charge_ah
    if r.remaining_ah is not None:
        b.remaining_ah = r.remaining_ah
        b.soc = soc_pct(b.remaining_ah, b.full_charge_capacity_ah)
    elif r.soc is not None:
        b.soc = r.soc
        b.remaining_ah = r.soc / 100.0 * b.full_charge_capacity_ah
    if r.full_charge_ah or r.rated_ah:
        b.soh = soh_pct(b.full_charge_capacity_ah, b.rated_capacity_ah)
    elif r.soh is not None:
        b.soh = r.soh
        b.full_charge_capacity_ah = r.soh / 100.0 * b.rated_capacity_ah
    if r.cycle_count is not None:
        b.cycle_count = r.cycle_count
    if r.fault_count is not None:
        b.fault_count = r.fault_count
    if r.operating_hours is not None:
        b.operating_hours = r.operating_hours
    if r.supply_voltage_v is not None:
        b.supply_voltage_v = r.supply_voltage_v
    if r.station_id and not b.station_id:
        b.station_id = r.station_id
    if r.slot_id and not b.locker_id:
        b.locker_id = r.slot_id
    b.last_seen_at = now
    apply_health(b)

    db.add(
        TelemetryReading(
            pack_id=b.id,
            ts=ts,
            received_at=now,
            station_id=b.station_id,
            slot_id=b.locker_id,
            voltage=b.voltage,
            current_a=b.current_a,
            temperature_c=b.temperature_c,
            soc=b.soc,
            soh=b.soh,
            remaining_ah=b.remaining_ah,
            cycle_count=b.cycle_count,
            supply_voltage_v=b.supply_voltage_v,
            health_score=b.health_score,
        )
    )

    result = IngestResult(b.id, health_score=b.health_score)
    if b.temperature_c > s.lockout_c:
        result.lockout = result.trip_relay = True
        if b.state != BatteryState.LOCKED:
            lock_pack(db, b, f"{b.temperature_c} C > {s.lockout_c} C")
    elif b.temperature_c > s.overheat_trip_c:
        result.trip_relay = True
        raise_alert(
            db,
            AlertType.OVER_TEMP,
            AlertSeverity.WARNING,
            f"Pack {b.id} at {b.temperature_c} C",
            station_id=b.station_id,
            pack_id=b.id,
        )
        send_command(b.station_id, b.locker_id, {"command": "TRIP_RELAY", "pack_id": b.id})
    else:
        resolve_alerts(db, AlertType.OVER_TEMP, station_id=b.station_id, pack_id=b.id)

    if b.supply_voltage_v is not None and b.supply_voltage_v < s.low_supply_v:
        raise_alert(
            db,
            AlertType.LOW_SUPPLY,
            AlertSeverity.WARNING,
            f"Low mains voltage {b.supply_voltage_v} V at {b.station_id or 'unknown station'}",
            station_id=b.station_id,
            pack_id=None,
        )
    elif b.supply_voltage_v is not None:
        resolve_alerts(db, AlertType.LOW_SUPPLY, station_id=b.station_id, pack_id=None)

    if b.state == BatteryState.CHARGING and b.soc >= 95 and b.temperature_c <= s.lockout_c:
        b.state = BatteryState.CHARGED
    evaluate_pack_health(db, b)
    if b.station_id:
        st = db.get(Station, b.station_id)
        if st:
            db.flush()
            check_stock_alert(db, st)
    db.commit()

    TELEMETRY_MSGS.labels("accepted").inc()
    for f in frames_for(b):
        hub.publish(f"pack:{b.id}", to_wire(f))
    return result


def ingest_telemetry(body: TelemetryIn, db: Session):
    try:
        res = ingest_reading(db, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {
        "pack_id": res.pack_id,
        "duplicate": res.duplicate,
        "trip_relay": res.trip_relay,
        "lockout": res.lockout,
        "health_score": res.health_score,
    }


def get_all_latest_telemetry(db: Session):
    packs = list(db.scalars(select(Battery).order_by(Battery.id).limit(200)))
    return {
        "count": len(packs),
        "packs": [
            {
                "pack_id": b.id,
                "station_id": b.station_id,
                "slot_id": b.locker_id,
                "state": b.state.value,
                "voltage": b.voltage,
                "current_a": b.current_a,
                "temperature_c": b.temperature_c,
                "soc": b.soc,
                "soh": b.soh,
                "cycle_count": b.cycle_count,
                "health_score": b.health_score,
                "health_status": b.health_status.value,
                "last_seen_at": b.last_seen_at.isoformat() if b.last_seen_at else None,
            }
            for b in packs
        ],
    }


def latest_telemetry(pack_id: str, db: Session):
    row = db.scalar(
        select(TelemetryReading)
        .where(TelemetryReading.pack_id == pack_id)
        .order_by(TelemetryReading.ts.desc())
    )
    if not row:
        raise HTTPException(404, "No telemetry recorded for this pack")
    return row
