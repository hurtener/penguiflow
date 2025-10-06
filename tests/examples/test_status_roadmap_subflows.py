from __future__ import annotations

import asyncio

import pytest

from examples.roadmap_status_updates_subflows.flow import (
    BUG_STEPS,
    CHUNK_BUFFER,
    DOCUMENT_STEPS,
    STATUS_BUFFER,
    BugState,
    DocumentState,
    UserQuery,
    build_diagnostics_playbook,
    build_flow,
    build_metadata_playbook,
    reset_buffers,
)
from penguiflow import Headers, Message
from penguiflow.types import FinalAnswer


async def _run_flow(query: str) -> tuple[FinalAnswer, Message]:
    flow, registry = build_flow()
    flow.run(registry=registry)
    message = Message(payload=UserQuery(text=query), headers=Headers(tenant="acme"))
    try:
        await flow.emit(message)
        final = await asyncio.wait_for(flow.fetch(), timeout=2.0)
        assert isinstance(final, FinalAnswer)
        return final, message
    finally:
        await flow.stop()


@pytest.mark.asyncio
async def test_document_branch_subflow_enriches_metadata() -> None:
    reset_buffers()
    final, message = await _run_flow("Summarize release notes")

    trace_id = message.trace_id
    statuses = STATUS_BUFFER[trace_id]
    metadata_step = DOCUMENT_STEPS[1].id
    assert any(
        update.message == "Launching metadata subflow"
        and update.roadmap_step_id == metadata_step
        for update in statuses
    )

    assert "Route: documents" in final.text
    chunk_entries = CHUNK_BUFFER[trace_id]
    assert len(chunk_entries) == 2
    assert chunk_entries[-1].done is True

    metadata_flow, metadata_registry = build_metadata_playbook()
    metadata_flow.run(registry=metadata_registry)
    try:
        state = DocumentState(
            query=UserQuery(text="Summarize release notes"),
            steps=DOCUMENT_STEPS,
            sources=["README.md", "CHANGELOG.md"],
        )
        msg = Message(payload=state, headers=Headers(tenant="acme"))
        await metadata_flow.emit(msg)
        enriched = await asyncio.wait_for(metadata_flow.fetch(), timeout=2.0)
        assert isinstance(enriched, DocumentState)
        assert enriched.metadata
        assert all("digest=" in entry for entry in enriched.metadata)
    finally:
        await metadata_flow.stop()


@pytest.mark.asyncio
async def test_bug_branch_subflow_aggregates_diagnostics() -> None:
    reset_buffers()
    final, message = await _run_flow("Bug: checkout throws 500")

    trace_id = message.trace_id
    statuses = STATUS_BUFFER[trace_id]
    diagnostics_step = BUG_STEPS[1].id
    assert any(
        update.message == "Launching diagnostics subflow"
        and update.roadmap_step_id == diagnostics_step
        for update in statuses
    )

    assert "Route: bug" in final.text
    assert "Artifacts" in final.text

    diagnostics_flow, diagnostics_registry = build_diagnostics_playbook()
    diagnostics_flow.run(registry=diagnostics_registry)
    try:
        state = BugState(
            query=UserQuery(text="Bug: checkout throws 500"),
            steps=BUG_STEPS,
            logs=["seed"],
        )
        msg = Message(payload=state, headers=Headers(tenant="acme"))
        await diagnostics_flow.emit(msg)
        aggregated = await asyncio.wait_for(diagnostics_flow.fetch(), timeout=2.0)
        assert isinstance(aggregated, BugState)
        assert aggregated.checks == {"unit": "pass", "integration": "fail"}
        assert any("integration" in entry for entry in aggregated.logs)
    finally:
        await diagnostics_flow.stop()
