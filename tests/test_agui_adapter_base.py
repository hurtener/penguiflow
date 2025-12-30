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
