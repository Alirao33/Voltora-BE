"""Route definitions: websockets."""

from __future__ import annotations

from fastapi import APIRouter

from controllers import websockets as controller

router = APIRouter(tags=["websockets"])

router.add_api_websocket_route("/ws/telemetry/{pack_id}", controller.ws_telemetry)
router.add_api_websocket_route("/ws/alerts", controller.ws_alerts)
