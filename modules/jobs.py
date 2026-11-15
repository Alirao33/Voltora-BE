"""Business logic: jobs."""

from __future__ import annotations

import asyncio
import logging
from sqlalchemy import select
from typing import Any

from config.database import SessionLocal
from config.settings import get_settings
from models import Van
from modules.bookings import expire_stale_bookings
from modules.fleet import generate_plan
from modules.forecasting import drift_check, refresh_targets, train_and_register
from modules.orders import expire_stale_orders

log_jobs = logging.getLogger("voltora.jobs")


def retrain_forecast() -> dict[str, Any]:
    with SessionLocal() as db:
        drift = drift_check(db)
        if drift.get("retrain"):
            mv = train_and_register(db)
            return {"retrained": mv.id, "drift": drift}
        return {"retrained": None, "drift": drift}


def daily_targets_and_routes() -> dict[str, Any]:
    """Forecast 12h demand -> update S_target -> generate greedy rebalancing routes for active vans."""
    with SessionLocal() as db:
        recs = refresh_targets(db)
        planned: dict[str, Any] = {}
        for van in db.scalars(select(Van).where(Van.is_active.is_(True))):
            try:
                planned[van.name] = len(generate_plan(db, van))
            except Exception as exc:
                planned[van.name] = f"skipped: {exc}"
        return {"targets": len(recs), "routes": planned}


def sweep() -> dict[str, int]:
    with SessionLocal() as db:
        n = expire_stale_bookings(db)
        o = expire_stale_orders(db)
        db.commit()
        return {"bookings_expired": n, "orders_expired": o}


JOBS = {
    "retrain_forecast": retrain_forecast,
    "daily_targets_and_routes": daily_targets_and_routes,
    "sweep": sweep,
}


log_app = logging.getLogger("voltora.app")


async def sweeper_loop() -> None:
    interval = max(10, get_settings().sweeper_interval_s)
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(sweep)
        except Exception:
            log_app.exception("Background sweep failed")
