from __future__ import annotations

import pytest

from penguiflow.sessions.models import StateUpdate, TaskStatus, TaskType, UpdateType
from penguiflow.sessions.persistence import InMemorySessionStateStore


@pytest.mark.asyncio
async def test_inmemory_session_state_store_prunes_updates() -> None:
    store = InMemorySessionStateStore(max_updates_per_session=2)
    session_id = "s1"
    await store.save_update(
        StateUpdate(
            session_id=session_id,
            task_id="t1",
            update_type=UpdateType.STATUS_CHANGE,
            content={"status": TaskStatus.PENDING.value, "task_type": TaskType.FOREGROUND.value},
        )
    )
    await store.save_update(
        StateUpdate(
            session_id=session_id,
            task_id="t1",
            update_type=UpdateType.STATUS_CHANGE,
            content={"status": TaskStatus.RUNNING.value, "task_type": TaskType.FOREGROUND.value},
        )
    )
    await store.save_update(
        StateUpdate(
            session_id=session_id,
            task_id="t1",
            update_type=UpdateType.STATUS_CHANGE,
            content={"status": TaskStatus.COMPLETE.value, "task_type": TaskType.FOREGROUND.value},
        )
    )
    updates = await store.list_updates(session_id)
    assert len(updates) == 2
    assert updates[-1].content["status"] == TaskStatus.COMPLETE.value

