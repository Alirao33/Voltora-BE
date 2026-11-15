"""Business logic: system."""

from __future__ import annotations

from fastapi import HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from integrations.metrics import METRICS_REGISTRY
from utils.dates import now_utc

def health_live():
    return {"status": "ok", "time": now_utc().isoformat()}


def health_ready(db: Session):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(503, f"Database unavailable: {exc.__class__.__name__}")
    return {"status": "ready"}


def prometheus_metrics():
    return Response(generate_latest(METRICS_REGISTRY), media_type=CONTENT_TYPE_LATEST)
