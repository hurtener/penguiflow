"""PenguiFlow-specific AG-UI adapter implementation."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ag_ui.core import RunAgentInput

_LOGGER = logging.getLogger(__name__)

from penguiflow.cli.playground_wrapper import AgentWrapper, ChatResult  # noqa: E402
from penguiflow.planner import PlannerEvent  # noqa: E402
from penguiflow.sessions.models import TaskContextSnapshot, TaskStatus, TaskType  # noqa: E402
from penguiflow.sessions.projections import PlannerEventProjector  # noqa: E402
from penguiflow.state.models import StateUpdate, UpdateType  # noqa: E402
from penguiflow.steering import SteeringInbox  # noqa: E402

from .base import AGUIAdapter, AGUIEvent  # noqa: E402

_SENTINEL = object()


@dataclass
class _RunResult:
    result: ChatResult | None = None
    error: Exception | None = None


class PenguiFlowAdapter(AGUIAdapter):
    """Wrap AgentWrapper chat runs and emit AG-UI events."""

    def __init__(
        self,
        agent: AgentWrapper,
        *,
        artifact_url_prefix: str = "/artifacts",
        resource_url_prefix: str = "/resources",
        session_manager: Any | None = None,
    ) -> None:
        super().__init__()
        self._agent = agent
        self._artifact_url_prefix = artifact_url_prefix.rstrip("/")
        self._resource_url_prefix = resource_url_prefix.rstrip("/")
        self._session_manager = session_manager
        self._streamed_answer = False
        # State tracking for state_update emission
        self._session_id: str | None = None
        self._task_id: str | None = None
        self._trace_id: str | None = None

    async def run(self, input: RunAgentInput) -> AsyncIterator[AGUIEvent]:  # type: ignore[override,misc]
        self._streamed_answer = False

        # Initialize state tracking for state_update emission
        self._session_id = input.thread_id or f"session_{input.run_id}"
        self._task_id = input.run_id or f"task_{secrets.token_hex(8)}"
        self._trace_id = input.run_id

        llm_context, tool_context = _extract_forwarded_contexts(input)
        _LOGGER.info(
            "AG-UI run: messages=%d, thread_id=%s, run_id=%s",
            len(input.messages or []), input.thread_id, input.run_id,
        )
        for i, msg in enumerate(input.messages or []):
            role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, Mapping) else None)
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, Mapping) else None)
            _LOGGER.info("  message[%d]: role=%s, content_type=%s, content_preview=%s",
                        i, role, type(content).__name__, repr(content)[:100] if content else "None")
        query = _pick_query(input.messages)
        _LOGGER.info("AG-UI extracted query: %r", query[:200] if query else "(empty)")

        session = None
        steering: SteeringInbox | None = None
        if self._session_manager is not None and self._session_id is not None and self._task_id is not None:
            session = await self._session_manager.get_or_create(self._session_id)
            session.update_context(llm_context=dict(llm_context), tool_context=dict(tool_context))
            if hasattr(session, "set_turn_id"):
                session.set_turn_id(self._task_id)
            try:
                await session.ensure_capacity(TaskType.FOREGROUND)
            except RuntimeError as exc:
                raise RuntimeError(str(exc)) from exc

            snapshot = TaskContextSnapshot(
                session_id=self._session_id,
                task_id=self._task_id,
                trace_id=self._trace_id,
                query=query,
                llm_context=dict(llm_context),
                tool_context=dict(tool_context),
                spawn_reason="agui_chat",
            )
            await session.registry.create_task(
                session_id=self._session_id,
                task_type=TaskType.FOREGROUND,
                priority=0,
                context_snapshot=snapshot,
                description=query,
                trace_id=self._trace_id,
                task_id=self._task_id,
            )
            session._publish(
                StateUpdate(
                    session_id=self._session_id,
                    task_id=self._task_id,
                    trace_id=self._trace_id,
                    update_type=UpdateType.STATUS_CHANGE,
                    content={
                        "status": TaskStatus.PENDING.value,
                        "reason": "created",
                        "task_type": TaskType.FOREGROUND.value,
                    },
                )
            )
            await session.registry.update_status(self._task_id, TaskStatus.RUNNING)
            session._publish(
                StateUpdate(
                    session_id=self._session_id,
                    task_id=self._task_id,
                    trace_id=self._trace_id,
                    update_type=UpdateType.STATUS_CHANGE,
                    content={
                        "status": TaskStatus.RUNNING.value,
                        "reason": "running",
                        "task_type": TaskType.FOREGROUND.value,
                    },
                )
            )
            steering = SteeringInbox()
            session._steering_inboxes[self._task_id] = steering

        queue: asyncio.Queue[AGUIEvent | object] = asyncio.Queue()
        run_result = _RunResult()

        def _event_consumer(event: PlannerEvent, _trace_id: str | None) -> None:
            for mapped in self._convert_planner_event(event):
                queue.put_nowait(mapped)

        async def _run_chat() -> None:
            try:
                tool_context_for_agent: dict[str, Any] = dict(tool_context)
                if session is not None and self._task_id is not None:
                    tool_context_for_agent.update(
                        {
                            "task_id": self._task_id,
                            "turn_id": self._task_id,
                            "is_subagent": False,
                        }
                    )
                run_result.result = await self._agent.chat(
                    query=query,
                    session_id=self._session_id or input.thread_id,
                    llm_context=llm_context,
                    tool_context=tool_context_for_agent,
                    event_consumer=_event_consumer,
                    trace_id_hint=input.run_id,
                    steering=steering,
                )
            except Exception as exc:  # pragma: no cover - surfaced via run lifecycle
                run_result.error = exc
            finally:
                if session is not None and self._task_id is not None:
                    session._steering_inboxes.pop(self._task_id, None)
                await queue.put(_SENTINEL)

        asyncio.create_task(_run_chat())

        async def _stream() -> AsyncIterator[AGUIEvent]:
            # Emit initial state updates: PENDING → RUNNING
            yield self._make_status_update("PENDING", task_type="FOREGROUND")
            yield self._make_status_update("RUNNING", task_type="FOREGROUND")

            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item  # type: ignore[misc]

            if run_result.error is not None:
                # Emit FAILED state update
                if session is not None and self._task_id is not None and self._session_id is not None:
                    await session.registry.update_task(
                        self._task_id, status=TaskStatus.FAILED, error=str(run_result.error)
                    )
                    session._publish(
                        StateUpdate(
                            session_id=self._session_id,
                            task_id=self._task_id,
                            trace_id=self._trace_id,
                            update_type=UpdateType.STATUS_CHANGE,
                            content={
                                "status": TaskStatus.FAILED.value,
                                "reason": "failed",
                                "task_type": TaskType.FOREGROUND.value,
                            },
                        )
                    )
                yield self._make_status_update("FAILED", error=str(run_result.error))
                raise run_result.error

            result = run_result.result
            _LOGGER.info("AG-UI run complete: result=%s", result is not None)
            if result is None:
                if session is not None and self._task_id is not None and self._session_id is not None:
                    await session.registry.update_task(self._task_id, status=TaskStatus.COMPLETE)
                    session._publish(
                        StateUpdate(
                            session_id=self._session_id,
                            task_id=self._task_id,
                            trace_id=self._trace_id,
                            update_type=UpdateType.STATUS_CHANGE,
                            content={
                                "status": TaskStatus.COMPLETE.value,
                                "reason": "complete",
                                "task_type": TaskType.FOREGROUND.value,
                            },
                        )
                    )
                yield self._make_status_update("COMPLETE")
                return

            _LOGGER.info(
                "AG-UI result: answer=%s, pause=%s, streamed=%s",
                bool(result.answer), bool(result.pause), self._streamed_answer,
            )

            if result.pause:
                if session is not None and self._task_id is not None and self._session_id is not None:
                    await session.registry.update_task(
                        self._task_id, status=TaskStatus.PAUSED, trace_id=self._trace_id
                    )
                    session._publish(
                        StateUpdate(
                            session_id=self._session_id,
                            task_id=self._task_id,
                            trace_id=self._trace_id,
                            update_type=UpdateType.STATUS_CHANGE,
                            content={
                                "status": TaskStatus.PAUSED.value,
                                "reason": "paused",
                                "task_type": TaskType.FOREGROUND.value,
                            },
                        )
                    )
                yield self._make_status_update("PAUSED", reason=result.pause.get("reason", "pause"))
                yield self.custom("pause", result.pause)
                pause_message = _format_pause_message(result.pause)
                for event in self._emit_text_block(pause_message):
                    yield event
                return

            if result.answer and not self._streamed_answer:
                answer_preview = result.answer[:200] if result.answer else "(empty)"
                _LOGGER.info("AG-UI emitting final answer: %s", answer_preview)
                for event in self._emit_text_block(result.answer):
                    yield event

            # Emit COMPLETE state update
            if session is not None and self._task_id is not None and self._session_id is not None:
                await session.registry.update_task(
                    self._task_id, status=TaskStatus.COMPLETE, result=result.answer, trace_id=self._trace_id
                )
                session._publish(
                    StateUpdate(
                        session_id=self._session_id,
                        task_id=self._task_id,
                        trace_id=self._trace_id,
                        update_type=UpdateType.STATUS_CHANGE,
                        content={
                            "status": TaskStatus.COMPLETE.value,
                            "reason": "complete",
                            "task_type": TaskType.FOREGROUND.value,
                        },
                    )
                )
            yield self._make_status_update("COMPLETE")

        initial_state = getattr(input, "state", None)
        if not isinstance(initial_state, Mapping):
            initial_state = None
        async for event in self.with_run_lifecycle(
            input, _stream(), initial_state=dict(initial_state) if initial_state else None
        ):
            _LOGGER.info("AG-UI yielding event: type=%s", getattr(event, 'type', type(event).__name__))
            yield event

    async def resume(
        self,
        *,
        resume_token: str,
        thread_id: str,
        run_id: str,
        user_input: str | None = None,
        tool_context: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[AGUIEvent]:
        self._streamed_answer = False

        # Initialize state tracking for state_update emission
        self._session_id = thread_id or f"session_{run_id}"
        self._task_id = run_id or f"task_{id(self)}"
        self._trace_id = run_id

        queue: asyncio.Queue[AGUIEvent | object] = asyncio.Queue()
        run_result = _RunResult()

        def _event_consumer(event: PlannerEvent, _trace_id: str | None) -> None:
            for mapped in self._convert_planner_event(event):
                queue.put_nowait(mapped)

        async def _run_resume() -> None:
            try:
                run_result.result = await self._agent.resume(
                    resume_token,
                    session_id=thread_id,
                    user_input=user_input,
                    tool_context=tool_context,
                    event_consumer=_event_consumer,
                    trace_id_hint=run_id,
                )
            except Exception as exc:  # pragma: no cover - surfaced via run lifecycle
                run_result.error = exc
            finally:
                await queue.put(_SENTINEL)

        asyncio.create_task(_run_resume())

        async def _stream() -> AsyncIterator[AGUIEvent]:
            # Emit initial state updates: PENDING → RUNNING (resume from pause)
            yield self._make_status_update("RUNNING", task_type="FOREGROUND", resumed=True)

            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item  # type: ignore[misc]

            if run_result.error is not None:
                yield self._make_status_update("FAILED", error=str(run_result.error))
                raise run_result.error

            result = run_result.result
            if result is None:
                yield self._make_status_update("COMPLETE")
                return

            if result.pause:
                yield self._make_status_update("PAUSED", reason=result.pause.get("reason", "pause"))
                yield self.custom("pause", result.pause)
                pause_message = _format_pause_message(result.pause)
                for event in self._emit_text_block(pause_message):
                    yield event
                return

            if result.answer and not self._streamed_answer:
                for event in self._emit_text_block(result.answer):
                    yield event

            # Emit COMPLETE state update
            yield self._make_status_update("COMPLETE")

        input = RunAgentInput(
            thread_id=thread_id,
            run_id=run_id,
            messages=[],
            tools=[],
            context=[],
            state=None,
            forwarded_props=None,
        )
        async for event in self.with_run_lifecycle(input, _stream(), initial_state=None):
            yield event

    def _emit_text_block(self, text: str) -> Iterable[AGUIEvent]:
        if not text:
            return []

        events: list[AGUIEvent] = []
        if not self._message_started:
            events.append(self.text_start())
        events.append(self.text_content(text))
        events.append(self.text_end())
        return events

    def _make_status_update(self, status: str, **extra: Any) -> AGUIEvent:
        """Create a state_update custom event for task status changes."""
        if not self._session_id or not self._task_id:
            raise ValueError("session_id and task_id must be set")
        update = StateUpdate(
            session_id=self._session_id,
            task_id=self._task_id,
            trace_id=self._trace_id,
            update_type=UpdateType.STATUS_CHANGE,
            content={"status": status, **extra},
        )
        return self.custom("state_update", update.model_dump(mode="json"))

    def _convert_planner_event(self, event: PlannerEvent) -> list[AGUIEvent]:
        extra = dict(event.extra or {})
        mapped: list[AGUIEvent] = []

        # Emit state updates for task tracking (parallel to AG-UI events)
        if self._session_id and self._task_id:
            projector = PlannerEventProjector(
                session_id=self._session_id,
                task_id=self._task_id,
                trace_id=self._trace_id,
            )
            for state_update in projector.project(event):
                mapped.append(self.custom("state_update", state_update.model_dump(mode="json")))

        if event.event_type == "step_start":
            step_name = extra.get("step_name") or event.node_name or f"step_{event.trajectory_step}"
            mapped.append(self.step_start(step_name, **extra))
            return mapped

        if event.event_type == "step_complete":
            step_name = event.node_name or extra.get("step_name") or f"step_{event.trajectory_step}"
            mapped.append(self.step_end(step_name, **extra))
            return mapped

        if event.event_type == "llm_stream_chunk":
            channel = extra.get("channel")
            text = str(extra.get("text") or "")
            done = bool(extra.get("done"))
            phase = extra.get("phase")

            # DEBUG: Log llm_stream_chunk events
            _LOGGER.info(
                "convert_llm_stream_chunk",
                extra={
                    "channel": channel,
                    "text_len": len(text),
                    "done": done,
                    "phase": phase,
                    "message_started": self._message_started,
                    "streamed_answer": self._streamed_answer,
                },
            )

            # Emit thinking/observation content as CUSTOM events
            if channel == "thinking":
                if text:
                    mapped.append(self.custom("thinking", {
                        "text": text,
                        "phase": phase,
                        "done": done,
                    }))
                return mapped

            # Emit revision content as CUSTOM events
            if channel == "revision":
                if text:
                    mapped.append(self.custom("revision", {"text": text, "done": done}))
                return mapped

            # Handle answer channel - stream as text message
            if channel == "answer":
                if not self._message_started:
                    _LOGGER.info("agui_emitting_text_start", extra={"channel": channel})
                    mapped.append(self.text_start())
                if text:
                    _LOGGER.info("agui_emitting_text_content", extra={"text_len": len(text), "channel": channel})
                    mapped.append(self.text_content(text))
                # NOTE: Don't emit text_end() here on done=True - let with_run_lifecycle handle it
                # This prevents premature message end when there are multiple LLM calls
                self._streamed_answer = True
                return mapped

            return mapped

        if event.event_type == "tool_call_start":
            raw_id = extra.get("tool_call_id")
            tool_call_id = str(raw_id) if raw_id else None
            tool_name = str(extra.get("tool_name") or "")
            args_json = extra.get("args_json")
            if not self._message_started:
                mapped.append(self.text_start())
            start_event = self.tool_start(tool_name, tool_call_id=tool_call_id)
            mapped.append(start_event)
            tool_call_id = start_event.tool_call_id
            if args_json is not None:
                args_text = str(args_json)
                if args_text:
                    mapped.append(self.tool_args(tool_call_id, args_text))
            return mapped

        if event.event_type == "tool_call_end":
            raw_id = extra.get("tool_call_id")
            if raw_id:
                mapped.append(self.tool_end(str(raw_id)))
            return mapped

        if event.event_type == "tool_call_result":
            raw_id = extra.get("tool_call_id")
            if raw_id:
                result_json = extra.get("result_json")
                mapped.append(self.tool_result(str(raw_id), str(result_json)))
            return mapped

        if event.event_type == "artifact_chunk":
            mapped.append(self._artifact_chunk_custom_event(extra))
            return mapped

        if event.event_type == "artifact_stored":
            mapped.append(self._artifact_custom_event(extra))
            return mapped

        if event.event_type == "resource_updated":
            mapped.append(self._resource_custom_event(extra))
            return mapped

        return mapped

    def _artifact_custom_event(self, extra: Mapping[str, Any]) -> AGUIEvent:
        artifact_id = str(extra.get("artifact_id") or "")
        artifact = {
            "id": artifact_id,
            "mime_type": extra.get("mime_type"),
            "size_bytes": extra.get("size_bytes"),
            "filename": extra.get("artifact_filename") or extra.get("filename"),
            "source": extra.get("source") or {},
        }
        return self.custom(
            "artifact_stored",
            {
                "artifact": artifact,
                "download_url": f"{self._artifact_url_prefix}/{artifact_id}",
            },
        )

    def _artifact_chunk_custom_event(self, extra: Mapping[str, Any]) -> AGUIEvent:
        return self.custom(
            "artifact_chunk",
            {
                "stream_id": extra.get("stream_id"),
                "seq": extra.get("seq"),
                "done": extra.get("done", False),
                "artifact_type": extra.get("artifact_type"),
                "chunk": extra.get("chunk"),
                "meta": extra.get("meta", {}),
            },
        )

    def _resource_custom_event(self, extra: Mapping[str, Any]) -> AGUIEvent:
        uri = str(extra.get("uri") or "")
        namespace = str(extra.get("namespace") or "")
        encoded_uri = quote(uri, safe="")
        return self.custom(
            "resource_updated",
            {
                "namespace": namespace,
                "uri": uri,
                "read_url": f"{self._resource_url_prefix}/{namespace}/{encoded_uri}",
            },
        )


def _extract_forwarded_contexts(input: RunAgentInput) -> tuple[dict[str, Any], dict[str, Any]]:
    forwarded = getattr(input, "forwarded_props", None)
    if forwarded is None:
        forwarded = {}
    if not isinstance(forwarded, Mapping):
        return {}, {}
    pengui = forwarded.get("penguiflow") if isinstance(forwarded.get("penguiflow"), Mapping) else {}
    llm_context = pengui.get("llm_context") if isinstance(pengui.get("llm_context"), Mapping) else {}  # type: ignore[union-attr]
    tool_context = pengui.get("tool_context") if isinstance(pengui.get("tool_context"), Mapping) else {}  # type: ignore[union-attr]
    return dict(llm_context), dict(tool_context)  # type: ignore[arg-type]


def _pick_query(messages: Iterable[Any]) -> str:
    for msg in reversed(list(messages or [])):
        role = getattr(msg, "role", None)
        content = getattr(msg, "content", None)
        if role is None and isinstance(msg, Mapping):
            role = msg.get("role")
            content = msg.get("content")
        if role != "user":
            continue
        text = _extract_text_content(content)
        if text:
            return text
    return ""


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        text = content.get("text")
        if isinstance(text, str):
            return text
    text_attr = getattr(content, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    if isinstance(content, Iterable) and not isinstance(content, (str, bytes, Mapping)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return "\n".join(parts)
    return ""


def _format_pause_message(pause: Mapping[str, Any]) -> str:
    payload = pause.get("payload", {}) if isinstance(pause.get("payload"), Mapping) else {}
    auth_url = payload.get("auth_url") or payload.get("url") or ""
    provider = payload.get("provider") or ""
    reason = pause.get("reason") or "pause"

    body = f"Planner paused ({reason})"
    if provider:
        body += f" for {provider}"
    if auth_url:
        body += f"\n[Open auth link]({auth_url})"
    resume_token = pause.get("resume_token")
    if resume_token:
        body += f"\nResume token: `{resume_token}`"
    return body
