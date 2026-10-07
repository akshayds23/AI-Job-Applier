"""In-process pub/sub used to stream pipeline progress to dashboard clients."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any

from core.logging_config import get_logger

logger = get_logger("events")

_MAX_QUEUE = 200
_MAX_HISTORY = 100


class EventBus:
    """Fan-out broker: one bounded queue per connected subscriber.

    A slow or dead client can never block the pipeline - its queue simply drops
    the oldest event once full.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._history: dict[str, deque] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, user_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        async with self._lock:
            self._subscribers.setdefault(user_id, set()).add(queue)
        return queue

    async def unsubscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._subscribers.get(user_id)
            if subs:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(user_id, None)

    async def publish(self, user_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = {
            "type": event_type,
            "data": payload or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        history = self._history.setdefault(user_id, deque(maxlen=_MAX_HISTORY))
        history.append(event)

        for queue in list(self._subscribers.get(user_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:  # drop oldest, keep the stream moving
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.debug("Dropped event for saturated subscriber")

    def recent(self, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        return list(self._history.get(user_id, ()))[-limit:]

    def subscriber_count(self, user_id: str | None = None) -> int:
        if user_id is not None:
            return len(self._subscribers.get(user_id, ()))
        return sum(len(s) for s in self._subscribers.values())


event_bus = EventBus()
