from __future__ import annotations

import pytest

from examples.roadmap_status_updates.flow import (
    BUG_STEPS,
    CHUNK_BUFFER,
    DOCUMENT_STEPS,
    STATUS_BUFFER,
    UserQuery,
    build_flow,
    reset_buffers,
)
from penguiflow import Headers, Message
from penguiflow.types import FinalAnswer


@pytest.mark.asyncio
async def test_document_route_status_updates() -> None:
    reset_buffers()
    flow, registry = build_flow()
    flow.run(registry=registry)

    try:
        message = Message(
            payload=UserQuery(text="Summarize the internal docs"),
            headers=Headers(tenant="acme"),
        )
        trace_id = message.trace_id

        await flow.emit(message)
        result = await flow.fetch()
        assert isinstance(result, FinalAnswer)
        assert "Route: documents" in result.text

        statuses = STATUS_BUFFER[trace_id]
        assert statuses[0].message == "Determining message path"

        plan_update = next(update for update in statuses if update.roadmap_step_list)
        assert [step.id for step in plan_update.roadmap_step_list or []] == [
            step.id for step in DOCUMENT_STEPS
        ]

        doc_step = DOCUMENT_STEPS[0].id
        doc_updates = [
            update.roadmap_step_status
            for update in statuses
            if update.roadmap_step_id == doc_step
        ]
        assert doc_updates == ["running", "ok"]

        final_step = DOCUMENT_STEPS[-1].id
        final_updates = [
            update.roadmap_step_status
            for update in statuses
            if update.roadmap_step_id == final_step
        ]
        assert final_updates == ["running", "ok"]

        chunks = CHUNK_BUFFER[trace_id]
        assert len(chunks) == 2
        assert chunks[-1].done is True
    finally:
        await flow.stop()


@pytest.mark.asyncio
async def test_bug_route_generates_flow_response() -> None:
    reset_buffers()
    flow, registry = build_flow()
    flow.run(registry=registry)

    try:
        message = Message(
            payload=UserQuery(text="Bug: production error in checkout"),
            headers=Headers(tenant="acme"),
        )
        trace_id = message.trace_id

        await flow.emit(message)
        result = await flow.fetch()
        assert isinstance(result, FinalAnswer)
        assert "Route: bug" in result.text
        assert "Artifacts" in result.text

        statuses = STATUS_BUFFER[trace_id]
        assert any(update.message == "Routing to bug subflow" for update in statuses)

        plan_update = next(update for update in statuses if update.roadmap_step_list)
        assert [step.id for step in plan_update.roadmap_step_list or []] == [
            step.id for step in BUG_STEPS
        ]

        first_bug_step = BUG_STEPS[0].id
        bug_updates = [
            update.roadmap_step_status
            for update in statuses
            if update.roadmap_step_id == first_bug_step
        ]
        assert bug_updates == ["running", "ok"]

        final_step = BUG_STEPS[-1].id
        final_updates = [
            update.roadmap_step_status
            for update in statuses
            if update.roadmap_step_id == final_step
        ]
        assert final_updates == ["running", "ok"]
    finally:
        await flow.stop()
