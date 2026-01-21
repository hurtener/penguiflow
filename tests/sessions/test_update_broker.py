from __future__ import annotations

import pytest

from penguiflow.sessions.broker import UpdateBroker
from penguiflow.sessions.models import StateUpdate, UpdateType


@pytest.mark.asyncio
async def test_update_broker_filters_and_evicts_for_critical_updates() -> None:
    broker = UpdateBroker(max_queue_size=1)
    queue, unsubscribe = await broker.subscribe(task_ids=["t1"])
    filtered, filtered_unsub = await broker.subscribe(task_ids=["t1"], update_types=[UpdateType.STATUS_CHANGE])

    broker.publish(StateUpdate(session_id="s", task_id="t2", update_type=UpdateType.THINKING, content={"x": 1}))
    assert queue.empty()

    broker.publish(StateUpdate(session_id="s", task_id="t1", update_type=UpdateType.THINKING, content={"x": 1}))
    assert queue.qsize() == 1

    broker.publish(StateUpdate(session_id="s", task_id="t1", update_type=UpdateType.THINKING, content={"x": 99}))
    assert filtered.empty()

    # Non-critical updates are dropped when queue is full.
    broker.publish(StateUpdate(session_id="s", task_id="t1", update_type=UpdateType.THINKING, content={"x": 2}))
    assert queue.qsize() == 1
    first = queue.get_nowait()
    assert first.content == {"x": 1}

    broker.publish(StateUpdate(session_id="s", task_id="t1", update_type=UpdateType.THINKING, content={"x": 3}))
    broker.publish(
        StateUpdate(
            session_id="s",
            task_id="t1",
            update_type=UpdateType.STATUS_CHANGE,
            content={"status": "RUNNING"},
        )
    )

    # Critical updates evict the oldest entry if needed.
    assert queue.qsize() == 1
    latest = queue.get_nowait()
    assert latest.update_type == UpdateType.STATUS_CHANGE

    # Exercise defensive branches in publish().
    assert broker._subs  # noqa: SLF001 - test-only access
    sub = broker._subs[0]  # noqa: SLF001 - test-only access
    sub.queue.put_nowait(StateUpdate(session_id="s", task_id="t1", update_type=UpdateType.THINKING, content={"x": 10}))
    original_get_nowait = sub.queue.get_nowait
    original_put_nowait = sub.queue.put_nowait
    try:
        def _raise_empty() -> None:
            raise __import__("asyncio").QueueEmpty

        def _raise_full(_update: StateUpdate) -> None:
            raise __import__("asyncio").QueueFull

        sub.queue.get_nowait = _raise_empty  # type: ignore[method-assign]
        broker.publish(
            StateUpdate(
                session_id="s",
                task_id="t1",
                update_type=UpdateType.STATUS_CHANGE,
                content={"status": "PAUSED"},
            )
        )
        sub.queue.put_nowait = _raise_full  # type: ignore[method-assign]
        broker.publish(StateUpdate(session_id="s", task_id="t1", update_type=UpdateType.THINKING, content={"x": 11}))
    finally:
        sub.queue.get_nowait = original_get_nowait  # type: ignore[method-assign]
        sub.queue.put_nowait = original_put_nowait  # type: ignore[method-assign]

    await filtered_unsub()
    await unsubscribe()
