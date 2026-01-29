from __future__ import annotations

import asyncio

import pytest

from penguiflow.sessions.broker import UpdateBroker
from penguiflow.sessions.models import StateUpdate, UpdateType


@pytest.mark.asyncio
async def test_update_broker_drops_noncritical_when_full() -> None:
    broker = UpdateBroker(max_queue_size=1)
    queue, _unsubscribe = await broker.subscribe()
    update_a = StateUpdate(
        session_id="s1",
        task_id="t1",
        update_type=UpdateType.PROGRESS,
        content={"step": 1},
    )
    update_b = StateUpdate(
        session_id="s1",
        task_id="t1",
        update_type=UpdateType.PROGRESS,
        content={"step": 2},
    )
    broker.publish(update_a)
    broker.publish(update_b)
    stored = await asyncio.wait_for(queue.get(), timeout=0.2)
    assert stored.content == {"step": 1}


@pytest.mark.asyncio
async def test_update_broker_keeps_critical_when_full() -> None:
    broker = UpdateBroker(max_queue_size=1)
    queue, _unsubscribe = await broker.subscribe()
    update_a = StateUpdate(
        session_id="s1",
        task_id="t1",
        update_type=UpdateType.PROGRESS,
        content={"step": 1},
    )
    update_b = StateUpdate(
        session_id="s1",
        task_id="t1",
        update_type=UpdateType.RESULT,
        content={"answer": "done"},
    )
    broker.publish(update_a)
    broker.publish(update_b)
    stored = await asyncio.wait_for(queue.get(), timeout=0.2)
    assert stored.update_type == UpdateType.RESULT
