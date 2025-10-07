from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import pytest

from examples.status_roadmap_flow.flow import (
    CODE_STEPS,
    DATA_STEPS,
    FINAL_STEP,
    StatusUpdate,
    UserQuery,
    build_flow,
)
from penguiflow import Headers, Message, StreamChunk


async def _run_flow(
    query: str, *, session_id: str = "test-session"
) -> tuple[list[Any], Message]:
    flow = build_flow()
    flow.run()
    headers = Headers(tenant="acme")
    message = Message(
        payload=UserQuery(text=query, session_id=session_id), headers=headers
    )

    try:
        await flow.emit(message)
        outputs: list[Any] = []
        final_message: Message | None = None
        while final_message is None:
            item = await asyncio.wait_for(flow.fetch(), timeout=2.0)
            outputs.append(item)
            if isinstance(item, Message) and isinstance(item.payload, str):
                final_message = item
        assert final_message is not None
        return outputs, final_message
    finally:
        await flow.stop()


def _group_statuses(updates: Iterable[StatusUpdate]) -> dict[int, list[StatusUpdate]]:
    buckets: dict[int, list[StatusUpdate]] = defaultdict(list)
    for update in updates:
        if update.roadmap_step_id is not None:
            buckets[update.roadmap_step_id].append(update)
    return buckets


@pytest.mark.asyncio
async def test_code_branch_emits_full_roadmap() -> None:
    outputs, final_message = await _run_flow("Investigate checkout bug")

    status_updates = [item for item in outputs if isinstance(item, StatusUpdate)]
    assert status_updates
    assert status_updates[0].message == "Determining message path"

    roadmap_update = next(
        update for update in status_updates if update.roadmap_step_list
    )
    assert roadmap_update.roadmap_step_list == CODE_STEPS

    grouped = _group_statuses(status_updates)

    step1_updates = grouped[1]
    assert step1_updates[-1].roadmap_step_status == "ok"

    step2_messages = {update.message for update in grouped[2] if update.message}
    assert {"Inspecting app.py", "Inspecting payments.py"}.issubset(step2_messages)
    assert grouped[2][-1].roadmap_step_status == "ok"

    step3_updates = grouped[3]
    assert step3_updates[-1].roadmap_step_status == "ok"

    final_step_updates = grouped[FINAL_STEP.id]
    assert final_step_updates[-1].roadmap_step_status == "ok"
    assert final_step_updates[-1].message == "Done!"

    chunks = [
        item.payload
        for item in outputs
        if isinstance(item, Message) and isinstance(item.payload, StreamChunk)
    ]
    assert len(chunks) == 2
    assert chunks[-1].done is True

    assert isinstance(final_message.payload, str)
    assert "Final reply for session" in final_message.payload

    flow_response_dump = final_message.meta.get("flow_response")
    assert flow_response_dump
    assert flow_response_dump["raw_output"].startswith("Code analysis completed")
    assert len(flow_response_dump["artifacts"]["insights"]) == 2


@pytest.mark.asyncio
async def test_data_branch_emits_full_roadmap() -> None:
    outputs, final_message = await _run_flow(
        "Summarise signup metrics", session_id="metrics-session"
    )

    status_updates = [item for item in outputs if isinstance(item, StatusUpdate)]
    assert status_updates[0].message == "Determining message path"

    roadmap_update = next(
        update for update in status_updates if update.roadmap_step_list
    )
    assert roadmap_update.roadmap_step_list == DATA_STEPS

    grouped = _group_statuses(status_updates)
    assert grouped[1][-1].roadmap_step_status == "ok"
    assert grouped[2][-1].roadmap_step_status == "ok"
    assert grouped[3][-1].roadmap_step_status == "ok"
    assert grouped[FINAL_STEP.id][-1].roadmap_step_status == "ok"

    final_text = final_message.payload
    assert isinstance(final_text, str)
    assert "Final reply for session metrics-session" in final_text

    flow_response_dump = final_message.meta.get("flow_response")
    assert flow_response_dump
    assert flow_response_dump["raw_output"].startswith("Data summary ready")
    artifacts = flow_response_dump["artifacts"]
    assert len(artifacts["metrics"]) == 2
    assert artifacts["chart"]["labels"] == ["daily_signups", "conversion_rate"]

    chunks = [
        item.payload
        for item in outputs
        if isinstance(item, Message) and isinstance(item.payload, StreamChunk)
    ]
    assert len(chunks) == 2
    assert chunks[-1].done is True
