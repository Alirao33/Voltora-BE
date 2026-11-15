"""Route definitions: alerts."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import alerts as controller
from guards.auth import require_admin
from schemas.alert import AlertOut

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/alerts", controller.list_alerts, methods=["GET"], response_model=list[AlertOut])
router.add_api_route("/alerts/{alert_id}/ack", controller.ack_alert, methods=["POST"], response_model=AlertOut)
router.add_api_route("/alerts/{alert_id}/resolve", controller.resolve_alert, methods=["POST"], response_model=AlertOut)
