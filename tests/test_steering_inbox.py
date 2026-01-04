from __future__ import annotations

import asyncio

import pytest

from penguiflow.steering import SteeringEvent, SteeringEventType, SteeringInbox


@pytest.mark.asyncio
async def test_steering_inbox_pause_resume() -> None:
    inbox = SteeringInbox()
    await inbox.push(
        SteeringEvent(
            session_id="s1",
            task_id="t1",
            event_type=SteeringEventType.PAUSE,
            payload={},
        )
    )

    unblocked = False

    async def waiter() -> None:
        nonlocal unblocked
        await inbox.wait_if_paused()
        unblocked = True

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)
    assert unblocked is False

    await inbox.push(
        SteeringEvent(
            session_id="s1",
            task_id="t1",
            event_type=SteeringEventType.RESUME,
            payload={},
        )
    )
    await asyncio.wait_for(task, timeout=0.2)
    assert unblocked is True


@pytest.mark.asyncio
async def test_steering_inbox_cancel_sets_flag() -> None:
    inbox = SteeringInbox()
    await inbox.push(
        SteeringEvent(
            session_id="s1",
            task_id="t1",
            event_type=SteeringEventType.CANCEL,
            payload={"reason": "stop"},
        )
    )
    assert inbox.cancelled is True
    assert inbox.cancel_reason == "stop"


@pytest.mark.asyncio
async def test_steering_inbox_queue_bound() -> None:
    inbox = SteeringInbox(maxsize=1)
    first = await inbox.push(
        SteeringEvent(
            session_id="s1",
            task_id="t1",
            event_type=SteeringEventType.INJECT_CONTEXT,
            payload={"text": "a"},
        )
    )
    second = await inbox.push(
        SteeringEvent(
            session_id="s1",
            task_id="t1",
            event_type=SteeringEventType.INJECT_CONTEXT,
            payload={"text": "b"},
        )
    )
    assert first is True
    assert second is False
