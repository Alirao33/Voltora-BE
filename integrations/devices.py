"""Infrastructure integration: devices."""

from __future__ import annotations

import logging
from typing import Callable, Optional

log_commands = logging.getLogger("voltora.commands")


_publisher: Optional[Callable[[str, str, dict], None]] = None


def register_publisher(fn: Optional[Callable[[str, str, dict], None]]) -> None:
    global _publisher
    _publisher = fn


def send_command(station_id: Optional[str], slot_id: Optional[str], command: dict) -> None:
    log_commands.warning("DEVICE COMMAND station=%s slot=%s %s", station_id, slot_id, command)
    if _publisher and station_id and slot_id:
        try:
            _publisher(station_id, slot_id, command)
        except Exception:
            log_commands.exception("Failed to publish device command")
