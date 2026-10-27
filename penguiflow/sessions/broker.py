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
        """Initialize the broker.

        Args:
            max_queue_size: Maximum size of each subscriber's queue. 0 means
                unbounded.
        """
        self._lock = asyncio.Lock()
        self._subs: list[_Subscription] = []
        self._max_queue_size = max_queue_size

    async def subscribe(
        self,
        *,
        task_ids: Iterable[str] | None = None,
        update_types: Iterable[UpdateType] | None = None,
    ) -> tuple[asyncio.Queue[StateUpdate], Callable[[], Awaitable[None]]]:
        """Register a new subscriber and return its queue plus an unsubscribe callback.

        Args:
            task_ids: If given, only updates for these task IDs are delivered to this
                subscriber. None means all task IDs are delivered.
            update_types: If given, only updates of these types are delivered to this
                subscriber. None means all update types are delivered.

        Returns:
            A tuple of `(queue, unsubscribe)`: the queue receives matching
            `StateUpdate`s, and calling `unsubscribe()` removes the subscription.
        """
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
        """Deliver an update to every matching subscriber's queue.

        Non-critical updates are dropped (not blocked on) when a subscriber's queue
        is full. Critical updates (RESULT, ERROR, NOTIFICATION, STATUS_CHANGE) evict
        the oldest queued item to make room instead of being dropped.

        Args:
            update: The state update to publish.
        """
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
