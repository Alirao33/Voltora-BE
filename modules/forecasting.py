"""Business logic: forecasting."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Any, Optional

from config.settings import get_settings
from constants.enums import OrderStatus
from constants.forecasting import DEFAULT_TEMP_C, HORIZON_HOURS
from models import ForecastModelVersion, ForecastRecord, Order, Station, User, WeatherObservation
from modules.audit import audit
from modules.forecast_engine import (
    forecast_demand,
    load,
    Row,
    save,
    SeasonalBaseline,
    target_stock,
    train,
)
from modules.pilot import run_pilot
from schemas.forecast import PilotRequest, TrainRequest, WeatherIn
from utils.access import scoped_station_id
from utils.dates import ensure_aware, now_utc, PKT

log_forecasting = logging.getLogger("voltora.forecast")


def _bucket(d: datetime) -> datetime:
    return d.astimezone(PKT).replace(minute=0, second=0, microsecond=0, tzinfo=None)


def build_rows(db: Session, days: int = 90) -> list[Row]:
    since = now_utc() - timedelta(days=days)
    counts: dict[tuple[str, datetime], int] = defaultdict(int)
    for o in db.scalars(
        select(Order).where(
            Order.created_at >= since,
            Order.status.in_((OrderStatus.PAID, OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED)),
        )
    ):
        counts[(o.station_id, _bucket(o.created_at))] += 1

    weather = {
        _bucket(w.ts): w
        for w in db.scalars(select(WeatherObservation).where(WeatherObservation.ts >= since))
    }
    rows = []
    for (sid, hour), n in counts.items():
        w = weather.get(hour)
        rows.append(Row(sid, hour, float(n), w.temp_c if w else DEFAULT_TEMP_C, w.rain_mm if w else 0.0))
    return rows


def _log_mlflow(mv: ForecastModelVersion) -> None:
    try:
        import mlflow

        with mlflow.start_run(run_name=f"demand_v{mv.id}"):
            mlflow.log_params({"algorithm": mv.algorithm, "n_rows": mv.n_rows})
            mlflow.log_metrics({k: v for k, v in mv.metrics.items() if isinstance(v, (int, float))})
    except Exception:
        log_forecasting.debug("MLflow logging skipped")


def train_and_register(
    db: Session, prefer_xgb: bool = True, rows: Optional[list[Row]] = None
) -> ForecastModelVersion:
    rows = rows if rows is not None else build_rows(db)
    if not rows:
        raise ValueError("No order history to train on yet")
    model, metrics = train(rows, prefer_xgb)
    os.makedirs(get_settings().model_dir, exist_ok=True)
    mv = ForecastModelVersion(algorithm=model.algorithm, metrics=metrics, artifact_path="", n_rows=len(rows))
    db.add(mv)
    db.flush()
    mv.artifact_path = os.path.join(get_settings().model_dir, f"demand_v{mv.id}.pkl")
    save(model, mv.artifact_path)
    for old in db.scalars(select(ForecastModelVersion).where(ForecastModelVersion.is_active.is_(True))):
        old.is_active = False
    mv.is_active = True
    db.commit()
    _log_mlflow(mv)
    return mv


def active_model(db: Session):
    mv = db.scalar(select(ForecastModelVersion).where(ForecastModelVersion.is_active.is_(True)))
    return (load(mv.artifact_path), mv) if mv and os.path.exists(mv.artifact_path) else (None, None)


def refresh_targets(db: Session, safety: float = 1.2) -> list[ForecastRecord]:
    """Forecast next-12-hour demand per station and update Station.target_stock."""
    model, mv = active_model(db)
    if model is None:
        model = SeasonalBaseline().fit(build_rows(db) or [Row("_", datetime(2000, 1, 3), 0.0)])
    start = _bucket(now_utc()) + timedelta(hours=1)
    weather = {
        _bucket(w.ts): {"temp_c": w.temp_c, "rain_mm": w.rain_mm}
        for w in db.scalars(select(WeatherObservation).where(WeatherObservation.ts >= now_utc() - timedelta(hours=1)))
    }
    out = []
    for st in db.scalars(select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))):
        demand = forecast_demand(model, st.id, start, HORIZON_HOURS, weather)
        target = target_stock(demand, capacity=max(st.total_lockers * 2, 1), safety=safety)
        st.target_stock = target
        rec = ForecastRecord(
            station_id=st.id,
            model_id=mv.id if mv else None,
            horizon_hours=HORIZON_HOURS,
            predicted_demand=round(demand, 3),
            target_stock=target,
        )
        db.add(rec)
        out.append(rec)
    db.commit()
    return out


def drift_check(db: Session, window_days: int = 7, tolerance: float = 1.5) -> dict[str, Any]:
    model, mv = active_model(db)
    if model is None:
        return {"status": "NO_MODEL", "retrain": True}
    recent = build_rows(db, window_days)
    if not recent:
        return {"status": "NO_RECENT_DATA", "retrain": False}
    mae = sum(abs(model.predict(r.station_id, r.ts, r.temp_c, r.rain_mm) - r.demand) for r in recent) / len(recent)
    ref = mv.metrics.get("mae")
    drift = ref is not None and ref > 0 and mae > ref * tolerance
    return {"status": "DRIFT" if drift else "OK", "recent_mae": round(mae, 4), "training_mae": ref, "retrain": bool(drift)}


def add_weather(body: WeatherIn, actor: User, db: Session):
    w = WeatherObservation(ts=ensure_aware(body.ts) if body.ts else now_utc(), temp_c=body.temp_c, rain_mm=body.rain_mm)
    db.add(w)
    db.commit()
    return {"id": w.id, "ts": w.ts}


def train_forecast(body: TrainRequest, actor: User, db: Session):
    try:
        mv = train_and_register(db, prefer_xgb=body.prefer_xgboost)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit(db, actor, "TRAIN_FORECAST", "forecast_model", mv.id, mv.metrics)
    db.commit()
    return {
        "model_id": mv.id,
        "algorithm": mv.algorithm,
        "metrics": mv.metrics,
        "n_rows": mv.n_rows,
    }


def refresh_forecast(actor: User, db: Session):
    recs = refresh_targets(db)
    audit(db, actor, "REFRESH_TARGETS", "station", "*", {"count": len(recs)})
    db.commit()
    return [
        {
            "station_id": r.station_id,
            "predicted_demand_12h": r.predicted_demand,
            "target_stock": r.target_stock,
        }
        for r in recs
    ]


def get_drift(db: Session):
    return drift_check(db)


def latest_forecast(actor: User, db: Session):
    latest: dict[str, ForecastRecord] = {}
    q = select(ForecastRecord).order_by(ForecastRecord.id.desc())
    target_station = scoped_station_id(actor)
    if target_station:
        q = q.where(ForecastRecord.station_id == target_station)
    for r in db.scalars(q.limit(100)):
        latest.setdefault(r.station_id, r)
    return [
        {
            "station_id": r.station_id,
            "horizon_hours": r.horizon_hours,
            "predicted_demand": r.predicted_demand,
            "target_stock": r.target_stock,
            "generated_at": r.generated_at,
        }
        for r in latest.values()
    ]


def simulate_pilot(body: PilotRequest, actor: User):
    return run_pilot(
        n_stations=body.n_stations,
        days=body.days,
        seed=body.seed,
        van_capacity=body.van_capacity,
    )
