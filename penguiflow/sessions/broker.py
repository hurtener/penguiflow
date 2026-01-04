"""Pub/sub broker for state updates within a streaming session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from .models import StateUpdate, UpdateType


@dataclass(slots=True)
class _Subscription:
    queue: asyncio.Queue[StateUpdate]
    task_ids: set[str] | None
    update_types: set[UpdateType] | None


class UpdateBroker:
    """In-memory pub/sub for state updates with bounded queues."""

    def __init__(self, *, max_queue_size: int = 0) -> None:
        self._lock = asyncio.Lock()
        self._subs: list[_Subscription] = []
        self._max_queue_size = max_queue_size

    async def subscribe(
        self,
        *,
        task_ids: Iterable[str] | None = None,
        update_types: Iterable[UpdateType] | None = None,
    ) -> tuple[asyncio.Queue[StateUpdate], Callable[[], Awaitable[None]]]:
        queue: asyncio.Queue[StateUpdate] = asyncio.Queue(maxsize=self._max_queue_size)
        sub = _Subscription(
            queue=queue,
            task_ids=set(task_ids) if task_ids else None,
            update_types=set(update_types) if update_types else None,
        )
        async with self._lock:
            self._subs.append(sub)

        async def _unsubscribe() -> None:
            async with self._lock:
                if sub in self._subs:
                    self._subs.remove(sub)

        return queue, _unsubscribe

    def publish(self, update: StateUpdate) -> None:
        critical_types = {UpdateType.RESULT, UpdateType.ERROR, UpdateType.NOTIFICATION, UpdateType.STATUS_CHANGE}
        for sub in list(self._subs):
            if sub.task_ids is not None and update.task_id not in sub.task_ids:
                continue
            if sub.update_types is not None and update.update_type not in sub.update_types:
                continue
            try:
                if sub.queue.full():
                    if update.update_type in critical_types:
                        try:
                            sub.queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    else:
                        continue
                sub.queue.put_nowait(update)
            except asyncio.QueueFull:
                continue


__all__ = ["UpdateBroker"]
