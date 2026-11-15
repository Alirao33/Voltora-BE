"""Infrastructure integration: hub."""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from typing import Optional

from config.settings import get_settings

def offer(q: asyncio.Queue, msg: dict) -> None:
    """Non-blocking enqueue: drop oldest frame if consumer is slow so telemetry producers never stall."""
    if q.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            q.get_nowait()
    q.put_nowait(msg)


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=get_settings().ws_queue_size)
        self._subs[topic].add(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        self._subs[topic].discard(q)
        if not self._subs[topic]:
            self._subs.pop(topic, None)

    def _broadcast(self, topic: str, msg: dict) -> None:
        for q in list(self._subs.get(topic, ())):
            offer(q, msg)

    def publish(self, topic: str, msg: dict) -> None:
        """Thread-safe publish callable from MQTT threads or FastAPI worker threads."""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._broadcast, topic, msg)


hub = Hub()
