from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from ag_ui.core import EventType, RunAgentInput

from penguiflow.agui_adapter.base import AGUIAdapter, AGUIEvent


class DummyAdapter(AGUIAdapter):
    async def run(self, input: RunAgentInput) -> AsyncIterator[AGUIEvent]:
        async def _events() -> AsyncIterator[AGUIEvent]:
            yield self.text_start()
            yield self.text_content("hello")
        async for event in self.with_run_lifecycle(input, _events()):
            yield event


@pytest.mark.asyncio
async def test_with_run_lifecycle_emits_start_and_finish() -> None:
    adapter = DummyAdapter()
    input_payload = RunAgentInput(
        thread_id="thread-1",
        run_id="run-1",
        messages=[],
        tools=[],
        context=[],
        state={},
        forwarded_props={},
    )

    events: list[AGUIEvent] = []
    async for event in adapter.run(input_payload):
        events.append(event)

    assert events[0].type == EventType.RUN_STARTED
    assert events[-1].type == EventType.RUN_FINISHED
    assert any(evt.type == EventType.TEXT_MESSAGE_START for evt in events)
    assert any(evt.type == EventType.TEXT_MESSAGE_END for evt in events)


def test_text_content_requires_start() -> None:
    adapter = DummyAdapter()
    with pytest.raises(RuntimeError):
        adapter.text_content("missing start")


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "add", "path": "/item", "value": {"name": "new"}},
        {"op": "remove", "path": "/item"},
        {"op": "replace", "path": "/item", "value": None},
        {"op": "move", "from": "/old", "path": "/item"},
        {"op": "copy", "from": "/old", "path": "/item"},
        {"op": "test", "path": "/item", "value": 42},
    ],
)
def test_state_delta_preserves_json_patch_wire_format(operation: dict) -> None:
    event = DummyAdapter().state_delta([operation])

    assert event.type == EventType.STATE_DELTA
    assert event.model_dump(mode="json", by_alias=True)["delta"] == [operation]


def test_state_helpers_preserve_patch_values() -> None:
    adapter = DummyAdapter()

    assert adapter.state_set("/item", None).model_dump(mode="json", by_alias=True)["delta"] == [
        {"op": "replace", "path": "/item", "value": None}
    ]
    assert adapter.state_add("/item", {"name": "new"}).model_dump(mode="json", by_alias=True)["delta"] == [
        {"op": "add", "path": "/item", "value": {"name": "new"}}
    ]


@pytest.mark.parametrize("metadata", [{}, {"attempt": 2, "detail": {"cached": False}}])
def test_step_helpers_preserve_metadata(metadata: dict) -> None:
    adapter = DummyAdapter()

    for event in (adapter.step_start("lookup", **metadata), adapter.step_end("lookup", **metadata)):
        payload = event.model_dump(mode="json", by_alias=True)
        assert payload["stepName"] == "lookup"
        assert payload.get("metadata") == (metadata or None)


@pytest.mark.asyncio
async def test_with_run_lifecycle_emits_run_error() -> None:
    class FailingAdapter(AGUIAdapter):
        async def run(self, input: RunAgentInput) -> AsyncIterator[AGUIEvent]:
            async def _events() -> AsyncIterator[AGUIEvent]:
                yield self.text_start()
                raise RuntimeError("boom")

            async for event in self.with_run_lifecycle(input, _events()):
                yield event

    adapter = FailingAdapter()
    input_payload = RunAgentInput(
        thread_id="thread-2",
        run_id="run-2",
        messages=[],
        tools=[],
        context=[],
        state={},
        forwarded_props={},
    )

    events: list[AGUIEvent] = []
    with pytest.raises(RuntimeError):
        async for event in adapter.run(input_payload):
            events.append(event)

    assert events[0].type == EventType.RUN_STARTED
    assert any(evt.type == EventType.RUN_ERROR for evt in events)
