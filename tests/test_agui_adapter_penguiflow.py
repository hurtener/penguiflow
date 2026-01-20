from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from ag_ui.core import EventType, RunAgentInput

from penguiflow.agui_adapter.penguiflow import PenguiFlowAdapter
from penguiflow.cli.playground_wrapper import AgentWrapper, ChatResult
from penguiflow.planner import PlannerEvent


class FakeAgentWrapper(AgentWrapper):
    def __init__(self, events: list[PlannerEvent]) -> None:
        self._events = events
        self.last_llm_context: dict[str, Any] | None = None
        self.last_tool_context: dict[str, Any] | None = None
        self.last_query: str | None = None

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def resume(
        self,
        resume_token: str,
        *,
        session_id: str,
        user_input: str | None = None,
        tool_context: Mapping[str, Any] | None = None,
        event_consumer: Any = None,
        trace_id_hint: str | None = None,
        steering: Any = None,
    ) -> ChatResult:
        del steering
        raise RuntimeError("resume not supported in FakeAgentWrapper")

    async def chat(
        self,
        query: str,
        *,
        session_id: str,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
        event_consumer: Any = None,
        trace_id_hint: str | None = None,
        steering: Any = None,
    ) -> ChatResult:
        del steering
        self.last_query = query
        self.last_llm_context = dict(llm_context or {})
        self.last_tool_context = dict(tool_context or {})
        if event_consumer:
            for event in self._events:
                event_consumer(event, trace_id_hint)
        return ChatResult(
            answer="Final answer",
            trace_id=trace_id_hint or "trace-1",
            session_id=session_id,
            metadata={},
            pause=None,
        )


@pytest.mark.asyncio
async def test_penguiflow_adapter_maps_events() -> None:
    planner_events = [
        PlannerEvent(
            event_type="step_start",
            ts=0.0,
            trajectory_step=0,
            extra={"action_seq": 1},
        ),
        PlannerEvent(
            event_type="tool_call_start",
            ts=0.01,
            trajectory_step=0,
            extra={
                "tool_call_id": "call_1_0",
                "tool_name": "search",
                "args_json": '{"query":"penguiflow"}',
            },
        ),
        PlannerEvent(
            event_type="tool_call_end",
            ts=0.02,
            trajectory_step=0,
            extra={"tool_call_id": "call_1_0"},
        ),
        PlannerEvent(
            event_type="tool_call_result",
            ts=0.03,
            trajectory_step=0,
            extra={"tool_call_id": "call_1_0", "result_json": '{"result":"ok"}'},
        ),
        PlannerEvent(
            event_type="llm_stream_chunk",
            ts=0.04,
            trajectory_step=0,
            extra={"text": "Hello", "done": False, "channel": "answer"},
        ),
        PlannerEvent(
            event_type="llm_stream_chunk",
            ts=0.05,
            trajectory_step=0,
            extra={"text": "", "done": True, "channel": "answer"},
        ),
        PlannerEvent(
            event_type="artifact_stored",
            ts=0.06,
            trajectory_step=0,
            extra={
                "artifact_id": "art-1",
                "mime_type": "text/plain",
                "size_bytes": 12,
                "artifact_filename": "note.txt",
                "source": {"namespace": "tools"},
            },
        ),
        PlannerEvent(
            event_type="artifact_chunk",
            ts=0.065,
            trajectory_step=0,
            extra={
                "stream_id": "ui",
                "seq": 0,
                "chunk": {"component": "markdown", "props": {"content": "Hello"}},
                "done": True,
                "artifact_type": "ui_component",
                "meta": {"source_tool": "render_component"},
            },
        ),
        PlannerEvent(
            event_type="resource_updated",
            ts=0.07,
            trajectory_step=0,
            extra={"namespace": "tools", "uri": "file:///test.txt"},
        ),
        PlannerEvent(
            event_type="step_complete",
            ts=0.08,
            trajectory_step=0,
            node_name="search",
        ),
    ]

    wrapper = FakeAgentWrapper(planner_events)
    adapter = PenguiFlowAdapter(wrapper)

    input_payload = RunAgentInput(
        thread_id="thread-1",
        run_id="run-1",
        messages=[{"id": "msg-1", "role": "user", "content": "Hi"}],
        tools=[],
        context=[],
        state={},
        forwarded_props={"penguiflow": {"llm_context": {"tone": "test"}, "tool_context": {"tenant_id": "t1"}}},
    )

    output_events = []
    async for event in adapter.run(input_payload):
        output_events.append(event)

    types = [event.type for event in output_events]
    assert EventType.RUN_STARTED in types
    assert EventType.RUN_FINISHED in types
    assert EventType.STEP_STARTED in types
    assert EventType.STEP_FINISHED in types
    assert EventType.TOOL_CALL_START in types
    assert EventType.TOOL_CALL_ARGS in types
    assert EventType.TOOL_CALL_END in types
    assert EventType.TOOL_CALL_RESULT in types
    assert EventType.TEXT_MESSAGE_CONTENT in types
    assert EventType.CUSTOM in types

    custom_events = [event for event in output_events if event.type == EventType.CUSTOM]
    assert any(event.name == "artifact_stored" for event in custom_events)
    assert any(event.name == "artifact_chunk" for event in custom_events)
    assert any(event.name == "resource_updated" for event in custom_events)

    message_chunks = [event for event in output_events if event.type == EventType.TEXT_MESSAGE_CONTENT]
    assert any(event.delta == "Hello" for event in message_chunks)

    assert wrapper.last_llm_context == {"tone": "test"}
    assert wrapper.last_tool_context == {"tenant_id": "t1"}
    assert wrapper.last_query == "Hi"


@pytest.mark.asyncio
async def test_penguiflow_adapter_extracts_text_from_content_list() -> None:
    wrapper = FakeAgentWrapper([])
    adapter = PenguiFlowAdapter(wrapper)

    input_payload = RunAgentInput(
        thread_id="thread-4",
        run_id="run-4",
        messages=[
            {
                "id": "msg-1",
                "role": "user",
                "content": [{"type": "text", "text": "Hello from list"}],
            }
        ],
        tools=[],
        context=[],
        state={},
        forwarded_props={},
    )

    async for _event in adapter.run(input_payload):
        pass

    assert wrapper.last_query == "Hello from list"


@pytest.mark.asyncio
async def test_agui_adapter_does_not_suppress_final_answer_on_empty_done_chunk() -> None:
    """Regression: providers may emit an answer-channel done marker with empty text.

    The adapter must not treat that as a streamed answer; otherwise it will skip
    emitting the final answer payload and the UI shows nothing.
    """

    class DoneOnlyChunkAgentWrapper(AgentWrapper):
        async def initialize(self) -> None:
            pass

        async def shutdown(self) -> None:
            pass

        async def resume(
            self,
            resume_token: str,
            *,
            session_id: str,
            user_input: str | None = None,
            tool_context: Mapping[str, Any] | None = None,
            event_consumer: Any = None,
            trace_id_hint: str | None = None,
            steering: Any = None,
        ) -> ChatResult:
            del resume_token, session_id, user_input, tool_context, event_consumer, trace_id_hint, steering
            raise RuntimeError("resume not supported in DoneOnlyChunkAgentWrapper")

        async def chat(
            self,
            query: str,
            *,
            session_id: str,
            llm_context: Mapping[str, Any] | None = None,
            tool_context: Mapping[str, Any] | None = None,
            event_consumer: Any = None,
            trace_id_hint: str | None = None,
            steering: Any = None,
        ) -> ChatResult:
            del query, llm_context, tool_context, steering
            if event_consumer is not None:
                event_consumer(
                    PlannerEvent(
                        event_type="llm_stream_chunk",
                        ts=0.01,
                        trajectory_step=0,
                        extra={"channel": "answer", "text": "", "done": True, "phase": "answer"},
                    ),
                    trace_id_hint,
                )
            return ChatResult(
                answer="Final answer should be emitted.",
                trace_id=trace_id_hint or "trace",
                session_id=session_id,
                metadata={},
                pause=None,
            )

    wrapper = DoneOnlyChunkAgentWrapper()
    adapter = PenguiFlowAdapter(wrapper)

    input_payload = RunAgentInput(
        thread_id="thread-x",
        run_id="run-x",
        messages=[{"id": "msg-1", "role": "user", "content": "Hi"}],
        tools=[],
        context=[],
        state={},
        forwarded_props={},
    )

    output_events = []
    async for event in adapter.run(input_payload):
        output_events.append(event)

    message_chunks = [event for event in output_events if event.type == EventType.TEXT_MESSAGE_CONTENT]
    assert any(event.delta == "Final answer should be emitted." for event in message_chunks)


@pytest.mark.asyncio
async def test_penguiflow_adapter_emits_pause_custom_event() -> None:
    class PauseAgentWrapper(AgentWrapper):
        async def initialize(self) -> None:
            pass

        async def shutdown(self) -> None:
            pass

        async def resume(
            self,
            resume_token: str,
            *,
            session_id: str,
            user_input: str | None = None,
            tool_context: Mapping[str, Any] | None = None,
            event_consumer: Any = None,
            trace_id_hint: str | None = None,
            steering: Any = None,
        ) -> ChatResult:
            del steering
            raise RuntimeError("resume not supported in PauseAgentWrapper")

        async def chat(
            self,
            query: str,
            *,
            session_id: str,
            llm_context: Mapping[str, Any] | None = None,
            tool_context: Mapping[str, Any] | None = None,
            event_consumer: Any = None,
            trace_id_hint: str | None = None,
            steering: Any = None,
        ) -> ChatResult:
            del steering
            return ChatResult(
                answer=None,
                trace_id=trace_id_hint or "trace-2",
                session_id=session_id,
                metadata={},
                pause={
                    "reason": "oauth",
                    "payload": {"provider": "github", "auth_url": "https://example.com"},
                    "resume_token": "resume-123",
                },
            )

    adapter = PenguiFlowAdapter(PauseAgentWrapper())
    input_payload = RunAgentInput(
        thread_id="thread-2",
        run_id="run-2",
        messages=[{"id": "msg-1", "role": "user", "content": "Hi"}],
        tools=[],
        context=[],
        state={},
        forwarded_props={},
    )

    output_events = []
    async for event in adapter.run(input_payload):
        output_events.append(event)

    custom_events = [event for event in output_events if event.type == EventType.CUSTOM]
    assert any(event.name == "pause" for event in custom_events)

    message_chunks = [event for event in output_events if event.type == EventType.TEXT_MESSAGE_CONTENT]
    assert any("Planner paused" in event.delta for event in message_chunks)


@pytest.mark.asyncio
async def test_penguiflow_adapter_emits_run_error() -> None:
    class ErrorAgentWrapper(AgentWrapper):
        async def initialize(self) -> None:
            pass

        async def shutdown(self) -> None:
            pass

        async def resume(
            self,
            resume_token: str,
            *,
            session_id: str,
            user_input: str | None = None,
            tool_context: Mapping[str, Any] | None = None,
            event_consumer: Any = None,
            trace_id_hint: str | None = None,
            steering: Any = None,
        ) -> ChatResult:
            del steering
            raise RuntimeError("resume not supported in ErrorAgentWrapper")

        async def chat(
            self,
            query: str,
            *,
            session_id: str,
            llm_context: Mapping[str, Any] | None = None,
            tool_context: Mapping[str, Any] | None = None,
            event_consumer: Any = None,
            trace_id_hint: str | None = None,
            steering: Any = None,
        ) -> ChatResult:
            del steering
            raise RuntimeError("boom")

    adapter = PenguiFlowAdapter(ErrorAgentWrapper())
    input_payload = RunAgentInput(
        thread_id="thread-3",
        run_id="run-3",
        messages=[{"id": "msg-1", "role": "user", "content": "Hi"}],
        tools=[],
        context=[],
        state={},
        forwarded_props={},
    )

    events: list[Any] = []
    with pytest.raises(RuntimeError):
        async for event in adapter.run(input_payload):
            events.append(event)

    assert any(event.type == EventType.RUN_ERROR for event in events)


@pytest.mark.asyncio
async def test_penguiflow_adapter_registers_foreground_task_when_session_manager_provided() -> None:
    class FakeRegistry:
        def __init__(self) -> None:
            self.created: list[dict[str, Any]] = []
            self.status_updates: list[tuple[str, str]] = []
            self.task_updates: list[dict[str, Any]] = []

        async def create_task(self, **kwargs: Any) -> None:
            self.created.append(dict(kwargs))

        async def update_status(self, task_id: str, status: Any) -> None:
            self.status_updates.append((task_id, str(status)))

        async def update_task(self, task_id: str, **kwargs: Any) -> None:
            payload = {"task_id": task_id, **kwargs}
            self.task_updates.append(payload)

    class FakeSession:
        def __init__(self, session_id: str) -> None:
            self.session_id = session_id
            self.registry = FakeRegistry()
            self.context_updates: list[dict[str, Any]] = []
            self.published: list[dict[str, Any]] = []
            self._steering_inboxes: dict[str, Any] = {}
            self.turn_ids: list[str | None] = []

        async def ensure_capacity(self, *_: Any, **__: Any) -> None:
            return None

        def update_context(self, *, llm_context: dict[str, Any], tool_context: dict[str, Any]) -> None:
            self.context_updates.append({"llm_context": dict(llm_context), "tool_context": dict(tool_context)})

        def set_turn_id(self, turn_id: str | None) -> None:
            self.turn_ids.append(turn_id)

        def _publish(self, update: Any) -> None:
            self.published.append(update.model_dump(mode="json"))

    class FakeSessionManager:
        def __init__(self) -> None:
            self.sessions: dict[str, FakeSession] = {}

        async def get_or_create(self, session_id: str) -> FakeSession:
            if session_id not in self.sessions:
                self.sessions[session_id] = FakeSession(session_id)
            return self.sessions[session_id]

    session_manager = FakeSessionManager()
    wrapper = FakeAgentWrapper([])
    adapter = PenguiFlowAdapter(wrapper, session_manager=session_manager)

    input_payload = RunAgentInput(
        thread_id="thread-1",
        run_id="run-1",
        messages=[{"id": "msg-1", "role": "user", "content": "Hi"}],
        tools=[],
        context=[],
        state={},
        forwarded_props={"penguiflow": {"llm_context": {"tone": "test"}, "tool_context": {"tenant_id": "t1"}}},
    )

    async for _event in adapter.run(input_payload):
        pass

    session = session_manager.sessions["thread-1"]
    assert session.context_updates == [{"llm_context": {"tone": "test"}, "tool_context": {"tenant_id": "t1"}}]
    assert session.turn_ids == ["run-1"]
    assert session.registry.created and session.registry.created[0]["task_id"] == "run-1"
    assert any(update.get("content", {}).get("status") == "RUNNING" for update in session.published)
    assert wrapper.last_tool_context is not None
    assert wrapper.last_tool_context.get("task_id") == "run-1"
    assert wrapper.last_tool_context.get("is_subagent") is False
