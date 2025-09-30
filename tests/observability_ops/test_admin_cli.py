"""Tests for the penguiflow-admin developer CLI."""

from __future__ import annotations

import asyncio
import json

import pytest

from penguiflow.admin import load_state_store, main, render_events
from penguiflow.state import StoredEvent

from . import sample_state_store


@pytest.mark.asyncio
async def test_render_events_tail_filters_history() -> None:
    sample_state_store.reset_state()
    store = sample_state_store.create_store()

    event_a = StoredEvent(
        trace_id="trace-123",
        ts=1.0,
        kind="remote_call_start",
        node_name="remote",
        node_id="remote-id",
        payload={
            "event": "remote_call_start",
            "trace_id": "trace-123",
            "remote_agent_url": "https://agent",
        },
    )
    event_b = StoredEvent(
        trace_id="trace-123",
        ts=2.0,
        kind="remote_call_success",
        node_name="remote",
        node_id="remote-id",
        payload={
            "event": "remote_call_success",
            "trace_id": "trace-123",
            "remote_status": "success",
        },
    )

    await store.save_event(event_a)
    await store.save_event(event_b)

    history = await store.load_history("trace-123")
    lines = render_events(history, tail=1)
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "remote_call_success"
    assert payload["remote_status"] == "success"


@pytest.mark.asyncio
async def test_cli_history_outputs_json(capsys: pytest.CaptureFixture[str]) -> None:
    sample_state_store.reset_state()
    store = await load_state_store(
        "tests.observability_ops.sample_state_store:create_store"
    )

    await store.save_event(
        StoredEvent(
            trace_id="trace-cli",
            ts=3.0,
            kind="remote_call_error",
            node_name="remote",
            node_id="remote-id",
            payload={
                "event": "remote_call_error",
                "trace_id": "trace-cli",
                "remote_status": "error",
            },
        )
    )

    exit_code = await asyncio.to_thread(
        main,
        [
            "history",
            "trace-cli",
            "--state-store",
            "tests.observability_ops.sample_state_store:create_store",
        ],
    )
    assert exit_code == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    payload = json.loads(out[0])
    assert payload["event"] == "remote_call_error"
    assert payload["remote_status"] == "error"


def test_cli_replay_includes_header(capsys: pytest.CaptureFixture[str]) -> None:
    sample_state_store.reset_state()
    sample_state_store._EVENTS["trace-replay"].append(
        StoredEvent(
            trace_id="trace-replay",
            ts=4.0,
            kind="remote_call_success",
            node_name="remote",
            node_id="remote-id",
            payload={
                "event": "remote_call_success",
                "trace_id": "trace-replay",
                "remote_status": "success",
            },
        )
    )

    exit_code = main(
        [
            "replay",
            "trace-replay",
            "--state-store",
            "tests.observability_ops.sample_state_store:create_store",
            "--delay",
            "0",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0].startswith("# replay trace=trace-replay")
    assert json.loads(out[1])["event"] == "remote_call_success"
