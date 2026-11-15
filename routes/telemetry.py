"""Route definitions: telemetry."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers import telemetry as controller
from guards.device import require_device_or_admin
from schemas.telemetry import TelemetryOut

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])

router.add_api_route("/ingest", controller.ingest_telemetry, methods=["POST"], dependencies=[Depends(require_device_or_admin)])
router.add_api_route("/latest", controller.get_all_latest_telemetry, methods=["GET"])
router.add_api_route("/{pack_id}/latest", controller.latest_telemetry, methods=["GET"], response_model=TelemetryOut)
