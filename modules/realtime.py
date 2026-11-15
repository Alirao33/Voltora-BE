"""Business logic: realtime."""

from __future__ import annotations

import asyncio
import contextlib
import json
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from config.database import SessionLocal
from config.settings import get_settings
from integrations.hub import hub
from models import Battery
from modules.auth import user_from_token
from modules.telemetry import ingest_reading, merge_partial, telemetry_frame
from utils.dates import now_utc
from utils.serialization import to_wire

async def stream_telemetry(ws: WebSocket, pack_id: str, can_publish: bool):
    await ws.accept()
    topic = f"pack:{pack_id}"
    queue = hub.subscribe(topic)
    heartbeat_s = get_settings().ws_heartbeat_s

    try:
        # Send immediate initial state frame if pack already exists
        with SessionLocal() as db:
            pack = db.get(Battery, pack_id)
            if pack:
                await ws.send_json(to_wire(telemetry_frame(pack)))

        async def _sender():
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                    await ws.send_json(msg)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "PING", "pack_id": pack_id, "timestamp": now_utc().isoformat()})

        async def _receiver():
            while True:
                raw_msg = await ws.receive_text()
                if not can_publish:
                    await ws.send_json({"type": "ERROR", "detail": "Read-only stream; provide device_key to ingest"})
                    continue
                try:
                    data = json.loads(raw_msg)
                    if data.get("type") == "PING":
                        await ws.send_json({"type": "PONG", "timestamp": now_utc().isoformat()})
                        continue
                    with SessionLocal() as db:
                        reading = merge_partial(db, pack_id, data)
                        res = ingest_reading(db, reading)
                    await ws.send_json(
                        to_wire(
                            {
                                "type": "ACK",
                                "pack_id": res.pack_id,
                                "duplicate": res.duplicate,
                                "trip_relay": res.trip_relay,
                                "lockout": res.lockout,
                            }
                        )
                    )
                except Exception as exc:
                    await ws.send_json({"type": "ERROR", "detail": str(exc)[:200]})

        sender_task = asyncio.create_task(_sender())
        receiver_task = asyncio.create_task(_receiver())
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(topic, queue)
        if ws.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await ws.close()


async def stream_alerts(ws: WebSocket):
    auth_header = ws.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.lower().startswith("bearer ") else ws.query_params.get("token")
    if not token:
        await ws.close(code=1008, reason="Admin token required")
        return

    with SessionLocal() as db:
        try:
            user = user_from_token(db, token)
            if not user.is_admin:
                await ws.close(code=1008, reason="Admin role required")
                return
        except HTTPException:
            await ws.close(code=1008, reason="Invalid token")
            return

    await ws.accept()
    queue = hub.subscribe("alerts")
    heartbeat_s = get_settings().ws_heartbeat_s
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                await ws.send_json(msg)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "PING", "timestamp": now_utc().isoformat()})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe("alerts", queue)
        if ws.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await ws.close()
