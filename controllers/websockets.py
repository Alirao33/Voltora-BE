"""Thin controllers (HTTP in, module call, HTTP out): websockets."""

from __future__ import annotations

from fastapi import WebSocket

from guards.device import ws_device_authorized
from modules import realtime as realtime_service

async def ws_telemetry(ws: WebSocket, pack_id: str):
    can_publish = ws_device_authorized(ws)
    await realtime_service.stream_telemetry(ws, pack_id, can_publish)


async def ws_alerts(ws: WebSocket):
    await realtime_service.stream_alerts(ws)
