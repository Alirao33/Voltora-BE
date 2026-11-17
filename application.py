"""Application factory, lifespan and middleware: application."""

from __future__ import annotations

import asyncio
import contextlib
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from config.database import init_db, SessionLocal
from config.settings import get_settings
from integrations.hub import hub
from integrations.metrics import HTTP_LATENCY, HTTP_REQUESTS
from integrations.mqtt import MqttIngestor
from modules.jobs import log_app, sweeper_loop
from modules.seeding import bootstrap_admin, seed_defaults, seed_demo
from routes import api_router
from utils.serialization import DualKeyJSONResponse

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    hub.bind_loop(asyncio.get_running_loop())
    init_db()

    with SessionLocal() as db:
        seed_defaults(db)
        bootstrap_admin(db)
        if s.seed_demo_data:
            seed_demo(db)

    mqtt_ingestor: Optional[MqttIngestor] = None
    if s.mqtt_enabled:
        try:
            mqtt_ingestor = MqttIngestor()
            mqtt_ingestor.start()
        except Exception:
            log_app.exception("Failed to start MQTT ingestor")

    sweeper_task = asyncio.create_task(sweeper_loop())
    log_app.info("Voltora Backend ready (env=%s)", s.env)
    try:
        yield
    finally:
        sweeper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper_task
        if mqtt_ingestor:
            mqtt_ingestor.stop()


async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path_label = route.path if route else request.url.path
    if path_label != "/metrics":
        elapsed = time.perf_counter() - start
        HTTP_REQUESTS.labels(request.method, path_label, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, path_label).observe(elapsed)
    return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="Voltora EV Battery Swapping Network API",
        version="2.0.0",
        default_response_class=DualKeyJSONResponse,
        lifespan=lifespan,
    )

    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(metrics_middleware)

    @app.exception_handler(OperationalError)
    async def database_unreachable(_: Request, exc: OperationalError):
        # A dropped database connection is not the user's fault: say so instead of a bare "Internal Server Error".
        return JSONResponse(status_code=503, content={"detail": "The database is not reachable right now. Please try again in a moment."})

    app.include_router(api_router)
    return app
