"""Infrastructure integration: mqtt."""

from __future__ import annotations

import json
import logging
from pydantic import ValidationError
from typing import Any

from config.database import SessionLocal
from config.settings import get_settings
from integrations.devices import register_publisher
from integrations.metrics import TELEMETRY_MSGS
from modules.telemetry import ingest_reading
from schemas.telemetry import TelemetryIn

log_mqtt_ingest = logging.getLogger("voltora.mqtt")


def handle_message(topic: str, payload: bytes) -> str:
    """Validate and store a single MQTT telemetry payload without crashing the network loop."""
    parts = topic.split("/")
    try:
        data = json.loads(payload)
        if len(parts) >= 4:
            data.setdefault("station_id", parts[1])
            data.setdefault("slot_id", parts[2])
        reading = TelemetryIn.model_validate(data)
        with SessionLocal() as db:
            res = ingest_reading(db, reading)
        return "duplicate" if res.duplicate else "accepted"
    except (json.JSONDecodeError, ValidationError, ValueError, LookupError, AttributeError, TypeError) as exc:
        TELEMETRY_MSGS.labels("rejected").inc()
        log_mqtt_ingest.warning("Rejected telemetry on %s: %s", topic, str(exc)[:160])
        return "rejected"
    except Exception:
        log_mqtt_ingest.exception("Telemetry handler failed on %s", topic)
        return "rejected"


class MqttIngestor:
    def __init__(self) -> None:
        import paho.mqtt.client as mqtt

        s = get_settings()
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            self.client = mqtt.Client()
        if s.mqtt_username:
            self.client.username_pw_set(s.mqtt_username, s.mqtt_password)
        self.client.on_connect = self._on_connect
        self.client.on_message = lambda c, u, m: handle_message(m.topic, m.payload)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    def _on_connect(self, client, userdata, flags, rc, *args):
        log_mqtt_ingest.info("MQTT connected (rc=%s); subscribing", rc)
        client.subscribe(get_settings().mqtt_telemetry_topic, qos=1)

    def publish_command(self, station_id: str, slot_id: str, command: dict[str, Any]) -> None:
        topic = get_settings().mqtt_command_topic.format(station_id=station_id, slot_id=slot_id)
        self.client.publish(topic, json.dumps(command), qos=1)

    def start(self) -> None:
        s = get_settings()
        self.client.connect_async(s.mqtt_host, s.mqtt_port, keepalive=30)
        self.client.loop_start()
        register_publisher(self.publish_command)

    def stop(self) -> None:
        register_publisher(None)
        self.client.loop_stop()
        self.client.disconnect()
