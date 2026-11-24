"""Route definitions: batteries."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import batteries as controller
from guards.auth import require_admin
from schemas.battery import BatteryOut
from schemas.telemetry import TelemetryOut

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/batteries", controller.list_batteries, methods=["GET"], response_model=list[BatteryOut])
router.add_api_route("/batteries", controller.create_battery, methods=["POST"], response_model=BatteryOut, status_code=201)
router.add_api_route("/batteries/{pack_id}", controller.update_battery, methods=["PATCH"], response_model=BatteryOut)
router.add_api_route("/batteries/second-life", controller.get_second_life_batteries, methods=["GET"], response_model=list[BatteryOut])
router.add_api_route("/batteries/{pack_id}", controller.get_battery, methods=["GET"], response_model=BatteryOut)  # after second-life, which would otherwise match {pack_id}
router.add_api_route("/telemetry/{pack_id}/history", controller.telemetry_history, methods=["GET"], response_model=list[TelemetryOut])
