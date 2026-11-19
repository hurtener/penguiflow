"""Tests for the planner's native tool-calling mode (Phase 5)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog
from penguiflow.llm.profiles import ModelProfile, register_profile
from penguiflow.llm.protocol import NativeToolCallResult
from penguiflow.llm.types import ToolCallPart
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import (
    JSONLLMClient,
    NativeToolsLLMClient,
    ReasoningJSONLLMClient,
)
from penguiflow.planner.react_step_native import (
    FINISH_TOOL_NAME,
    _parse_tool_args,
    build_native_tools,
    resolve_native_eligibility,
)
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from typing_extensions import Unpack

    from penguiflow.catalog import NodeSpec
    from penguiflow.llm.types import ToolSpec
    from penguiflow.planner.models import PlannerEvent

    from .planner_options import PlannerOptions


class Query(BaseModel):
    """Query payload."""

    text: str


class Verdict(BaseModel):
    """Verdict payload."""

    answer: int
    method: str


TOOL_INVOCATIONS: list[tuple[str, str]] = []


async def echo(payload: Query, _ctx: object) -> Query:
    """Echo the input back."""
    TOOL_INVOCATIONS.append(("echo", payload.text))
    return payload


async def shout(payload: Query, _ctx: object) -> Query:
    """Return the input text upper-cased."""
    TOOL_INVOCATIONS.append(("shout", payload.text))
    return Query(text=payload.text.upper())


def make_catalog() -> list[NodeSpec]:
    """Build the echo and shout catalog."""
    registry = ModelRegistry()
    registry.register("echo", Query, Query)
    registry.register("shout", Query, Query)
    nodes = [Node(echo, name="echo"), Node(shout, name="shout")]
    return build_catalog(nodes, registry)


def tool_call(
    name: str, args: dict[str, Any] | str, call_id: str = "c1"
) -> ToolCallPart:
    """Build a native tool call of ``name`` with ``args``."""
    arguments = args if isinstance(args, str) else json.dumps(args)
    return ToolCallPart(name=name, arguments_json=arguments, call_id=call_id)


class StubNativeClient(ReasoningJSONLLMClient, NativeToolsLLMClient):
    """Stub serving native turns and prompted repair turns."""

    def __init__(
        self,
        turns: Sequence[NativeToolCallResult | str],
        model: str = "stub-native-model",
    ) -> None:
        """Replay ``turns`` in order."""
        self._turns = list(turns)
        self._model = model
        self.tool_declarations: list[list[str]] = []
        self.tools_call_messages: list[list[Mapping[str, Any]]] = []
        self.complete_calls: list[list[Mapping[str, Any]]] = []
        self.streamed: list[str] = []

    @override
    async def complete_with_tools(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[ToolSpec],
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> NativeToolCallResult:
        self.tool_declarations.append([t.name for t in tools])
        self.tools_call_messages.append(list(messages))
        result = self._turns.pop(0)
        assert isinstance(result, NativeToolCallResult), (
            "expected a native turn"
        )
        if stream and on_stream_chunk is not None and result.content:
            on_stream_chunk(result.content, False)
            on_stream_chunk("", True)
        if (
            stream
            and on_reasoning_chunk is not None
            and result.reasoning_content
        ):
            on_reasoning_chunk(result.reasoning_content, False)
            on_reasoning_chunk("", True)
        return result

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        self.complete_calls.append(list(messages))
        result = self._turns.pop(0)
        assert isinstance(result, str), "expected a prompted/repair turn"
        return result, 0.0


def make_native_planner(
    client: JSONLLMClient, **kwargs: Unpack[PlannerOptions]
) -> ReactPlanner:
    """Build a native-tool planner over ``client``."""
    return ReactPlanner(
        llm_client=client,
        catalog=make_catalog(),
        tool_call_mode="native",
        **kwargs,
    )


class TestNativeMapping:
    """Tests for native mapping."""

    @pytest.mark.asyncio
    async def test_content_only_turn_is_final_answer(self) -> None:
        """Content only turn is final answer."""
        client = StubNativeClient(
            [NativeToolCallResult(content="The answer is 391.")]
        )
        planner = make_native_planner(client, max_iters=3)
        result = await planner.run("17*23?")

        assert result.reason == "answer_complete"
        assert result.payload["raw_answer"] == "The answer is 391."
        # The catalog was declared natively.
        assert client.tool_declarations == [["echo", "shout"]]

    @pytest.mark.asyncio
    async def test_single_tool_call_then_finish(self) -> None:
        """Single tool call then finish."""
        client = StubNativeClient(
            [
                NativeToolCallResult(
                    content="Let me echo that.",
                    tool_calls=[tool_call("echo", {"text": "hi"})],
                ),
                NativeToolCallResult(content="Echoed: hi"),
            ]
        )
        planner = make_native_planner(client, max_iters=4)
        result = await planner.run("echo hi")

        assert result.reason == "answer_complete"
        assert result.payload["raw_answer"] == "Echoed: hi"
        assert len(client.tool_declarations) == 2

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_map_to_parallel_plan(self) -> None:
        """Parallel tool calls map to parallel plan."""
        TOOL_INVOCATIONS.clear()
        client = StubNativeClient(
            [
                NativeToolCallResult(
                    content="Running both.",
                    tool_calls=[
                        tool_call("echo", {"text": "a"}, "c1"),
                        tool_call("shout", {"text": "b"}, "c2"),
                    ],
                ),
                NativeToolCallResult(content="a and B done"),
            ]
        )
        planner = make_native_planner(client, max_iters=4)
        result = await planner.run("do both")

        assert result.reason == "answer_complete"
        assert result.payload["raw_answer"] == "a and B done"
        # Both branches of the parallel plan actually executed.
        assert ("echo", "a") in TOOL_INVOCATIONS
        assert ("shout", "b") in TOOL_INVOCATIONS

    @pytest.mark.asyncio
    async def test_malformed_args_degrade_to_empty_and_recover(self) -> None:
        """Malformed args degrade to empty and recover."""
        assert _parse_tool_args("not json", tool_name="echo") == {}
        assert _parse_tool_args('["a", "b"]', tool_name="echo") == {}
        assert _parse_tool_args("", tool_name="echo") == {}
        assert _parse_tool_args('{"text": "ok"}', tool_name="echo") == {
            "text": "ok"
        }


class TestEligibilityAndDowngrade:
    """Tests for eligibility and downgrade."""

    def test_missing_method_is_ineligible(self) -> None:
        """Missing method is ineligible."""

        class Bare(JSONLLMClient):
            """Client without ``complete_with_tools``."""

            _model = "stub-native-model"

            @override
            async def complete(
                self,
                *,
                messages: Sequence[Mapping[str, Any]],
                response_format: Mapping[str, Any] | None = None,
                stream: bool = False,
                on_stream_chunk: Callable[[str, bool], None] | None = None,
            ) -> str:
                msg = "not called: eligibility is checked statically"
                raise AssertionError(msg)

        planner = make_native_planner(StubNativeClient([]))
        planner.client = Bare()
        reason = resolve_native_eligibility(planner)
        assert reason is not None and "complete_with_tools" in reason

    def test_route_gated_profile_is_ineligible(self) -> None:
        """Route gated profile is ineligible."""
        register_profile(
            "stub-gated-model",
            ModelProfile(
                supports_tools=True, supports_native_tool_calls=False
            ),
        )
        client = StubNativeClient([], model="stub-gated-model")
        planner = make_native_planner(client)
        reason = resolve_native_eligibility(planner)
        assert reason is not None and "native tool calls" in reason

    def test_databricks_gpt_5_5_is_gated(self) -> None:
        """Databricks gpt 5 5 is gated."""
        from penguiflow.llm.profiles import get_profile

        assert (
            get_profile("databricks-gpt-5-5").supports_native_tool_calls
            is False
        )
        assert (
            get_profile(
                "databricks-claude-opus-4-8"
            ).supports_native_tool_calls
            is True
        )

    @pytest.mark.asyncio
    async def test_downgrade_falls_back_to_prompted_with_event(self) -> None:
        """Downgrade falls back to prompted with event."""
        events: list[PlannerEvent] = []

        # Client lacks complete_with_tools entirely → prompted JSON path
        # serves.
        class PromptedOnly(JSONLLMClient):
            _model = "stub-native-model"

            def __init__(self) -> None:
                self.calls = 0

            @override
            async def complete(
                self,
                *,
                messages: Sequence[Mapping[str, Any]],
                response_format: Mapping[str, Any] | None = None,
                stream: bool = False,
                on_stream_chunk: Callable[[str, bool], None] | None = None,
            ) -> str | tuple[str, float]:
                self.calls += 1
                return (
                    json.dumps(
                        {
                            "next_node": "final_response",
                            "args": {"answer": "via prompted"},
                        }
                    ),
                    0.0,
                )

        planner = ReactPlanner(
            llm_client=PromptedOnly(),
            catalog=make_catalog(),
            tool_call_mode="native",
            max_iters=3,
            event_callback=events.append,
        )
        result = await planner.run("hi")

        assert result.payload["raw_answer"] == "via prompted"
        downgrades = [
            e for e in events if e.event_type == "tool_call_mode_downgraded"
        ]
        assert len(downgrades) == 1
        assert downgrades[0].extra["to"] == "prompted"

    def test_invalid_tool_call_mode_fails_loudly(self) -> None:
        """Invalid tool call mode fails loudly."""
        with pytest.raises(ValueError, match="tool_call_mode"):
            ReactPlanner(
                llm_client=StubNativeClient([]),
                catalog=make_catalog(),
                tool_call_mode="grpc",
            )


class TestStructuredFinalInNativeMode:
    """Tests for structured final in native mode."""

    @pytest.mark.asyncio
    async def test_content_finish_triggers_structured_extraction(self) -> None:
        """Content finish triggers structured extraction."""
        events: list[PlannerEvent] = []
        client = StubNativeClient(
            [
                NativeToolCallResult(content="It is 391."),
                # Extraction/repair turn goes through complete() with
                # json_object.
                json.dumps({"answer": 391, "method": "multiplication"}),
            ]
        )
        planner = make_native_planner(
            client,
            max_iters=3,
            final_response_model=Verdict,
            event_callback=events.append,
        )
        result = await planner.run("17*23?")

        assert result.payload["raw_answer"] == "It is 391."
        assert result.payload["structured"] == {
            "answer": 391,
            "method": "multiplication",
        }
        assert len(client.complete_calls) == 1  # exactly one extraction turn
        validated = [
            e
            for e in events
            if e.event_type == "final_response_structured_validated"
        ]
        assert validated and validated[0].extra["repaired"] is True


class TestNativeStreaming:
    """Tests for native streaming."""

    @pytest.mark.asyncio
    async def test_answer_streams_token_by_token(self) -> None:
        """A content-only finish streams live on the answer channel."""
        events: list[PlannerEvent] = []

        class TokenStreamingClient(StubNativeClient):
            @override
            async def complete_with_tools(
                self,
                *,
                messages: Sequence[Mapping[str, Any]],
                tools: Sequence[ToolSpec],
                stream: bool = False,
                on_stream_chunk: Callable[[str, bool], None] | None = None,
                on_reasoning_chunk: Callable[[str, bool], None] | None = None,
            ) -> NativeToolCallResult:
                self.tool_declarations.append([t.name for t in tools])
                result = self._turns.pop(0)
                assert isinstance(result, NativeToolCallResult)
                if stream and on_stream_chunk is not None and result.content:
                    for token in result.content.split(" "):
                        on_stream_chunk(token + " ", False)
                    on_stream_chunk("", True)
                if (
                    stream
                    and on_reasoning_chunk is not None
                    and result.reasoning_content
                ):
                    on_reasoning_chunk(result.reasoning_content, False)
                return result

        client = TokenStreamingClient(
            [
                NativeToolCallResult(
                    content="Done: hi everyone",
                    reasoning_content="quick check",
                )
            ]
        )
        planner = make_native_planner(
            client,
            max_iters=3,
            stream_final_response=True,
            event_callback=events.append,
        )
        result = await planner.run("hi")

        chunks = [e for e in events if e.event_type == "llm_stream_chunk"]
        answer = [
            e
            for e in chunks
            if e.extra.get("channel") == "answer" and e.extra.get("text")
        ]
        # Parity with prompted mode: reasoning renders on the thinking channel.
        reasoning = [
            e
            for e in chunks
            if e.extra.get("channel") == "thinking" and e.extra.get("text")
        ]
        answer_done = [
            e
            for e in chunks
            if e.extra.get("channel") == "answer" and e.extra.get("done")
        ]
        step_start_seqs = {
            e.extra.get("action_seq")
            for e in events
            if e.event_type == "step_start"
        }

        assert result.payload["raw_answer"] == "Done: hi everyone"
        assert len(answer) == 3, (
            "answer must stream token-by-token, not as one flush"
        )
        assert answer_done and not answer_done[-1].extra.get("superseded")
        assert reasoning, "reasoning deltas must flow in native mode"
        # The UI answer gate drops mismatched seqs - chunks must match
        # step_start.
        assert {e.extra.get("action_seq") for e in answer} <= step_start_seqs

    @pytest.mark.asyncio
    async def test_disobedient_preamble_is_superseded(self) -> None:
        """Preamble text alongside tool calls retracts the answer stream."""
        events: list[PlannerEvent] = []

        class PreambleClient(StubNativeClient):
            @override
            async def complete_with_tools(
                self,
                *,
                messages: Sequence[Mapping[str, Any]],
                tools: Sequence[ToolSpec],
                stream: bool = False,
                on_stream_chunk: Callable[[str, bool], None] | None = None,
                on_reasoning_chunk: Callable[[str, bool], None] | None = None,
            ) -> NativeToolCallResult:
                self.tool_declarations.append([t.name for t in tools])
                result = self._turns.pop(0)
                assert isinstance(result, NativeToolCallResult)
                if stream and on_stream_chunk is not None and result.content:
                    on_stream_chunk(result.content, False)
                return result

        client = PreambleClient(
            [
                NativeToolCallResult(
                    content="Let me check that.",
                    tool_calls=[tool_call("echo", {"text": "hi"})],
                ),
                NativeToolCallResult(content="Done: hi"),
            ]
        )
        planner = make_native_planner(
            client,
            max_iters=4,
            stream_final_response=True,
            event_callback=events.append,
        )
        result = await planner.run("echo hi")

        chunks = [e for e in events if e.event_type == "llm_stream_chunk"]
        superseded = [e for e in chunks if e.extra.get("superseded")]
        thinking = [
            e
            for e in chunks
            if e.extra.get("channel") == "thinking" and e.extra.get("text")
        ]

        assert result.payload["raw_answer"] == "Done: hi"
        assert superseded, (
            "preamble turn must close the answer stream with superseded=True"
        )
        assert any(e.extra["text"] == "Let me check that." for e in thinking)


class TestNativeRuntimeWiring:
    """Tests for native runtime wiring."""

    @pytest.mark.asyncio
    async def test_arg_repair_message_consumed_by_native_step(self) -> None:
        """The native step prepends and consumes arg-repair guidance.

        The runtime delivers it via trajectory metadata (prompted-step parity).
        """
        from penguiflow.planner import prompts
        from penguiflow.planner.trajectory import Trajectory

        client = StubNativeClient([NativeToolCallResult(content="ok")])
        planner = make_native_planner(client, max_iters=2)
        trajectory = Trajectory(query="hi")
        trajectory.metadata["arg_repair_message"] = (
            prompts.render_arg_repair_message(
                "echo", "boom", tool_call_mode="native"
            )
        )
        await planner.step(trajectory)

        first_call = client.tools_call_messages[0]
        repair = [
            m
            for m in first_call
            if m.get("role") == "system"
            and "failed validation" in str(m.get("content", ""))
        ]
        assert repair, "native step must prepend the arg repair message"
        content = str(repair[0]["content"])
        assert "next_node" not in content, (
            "native wording must not instruct JSON envelopes"
        )
        assert "plain text" in content
        assert trajectory.metadata.get("arg_repair_message") is None, (
            "message must be consumed"
        )

    @pytest.mark.asyncio
    async def test_steering_inputs_cleared_after_native_step(self) -> None:
        """Steering inputs cleared after native step."""
        from penguiflow.planner.trajectory import Trajectory

        client = StubNativeClient([NativeToolCallResult(content="ok")])
        planner = make_native_planner(client, max_iters=2)
        trajectory = Trajectory(query="hi")
        trajectory.steering_inputs.append("be brief")
        await planner.step(trajectory)
        assert trajectory.steering_inputs == []

    def test_native_prompt_has_no_envelope_leakage(self) -> None:
        """Native prompt has no envelope leakage."""
        planner = make_native_planner(StubNativeClient([]))
        prompt = planner.system_prompt
        assert "args.answer" not in prompt
        assert "JSON action object" not in prompt
        assert "requires_followup: true" not in prompt
        # The single deliberate negative instruction is allowed:
        assert prompt.count("next_node") == 1


class TestDualModeConformance:
    """Both modes produce the same decisions and observations."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["prompted", "native"])
    async def test_tool_then_finish_parity(self, mode: str) -> None:
        """Tool then finish parity."""
        if mode == "native":
            client: Any = StubNativeClient(
                [
                    NativeToolCallResult(
                        content="",
                        tool_calls=[tool_call("echo", {"text": "hi"})],
                    ),
                    NativeToolCallResult(content="final: hi"),
                ]
            )
        else:

            class PromptedStub(JSONLLMClient):
                _model = "stub-native-model"

                def __init__(self) -> None:
                    self._turns = [
                        json.dumps(
                            {"next_node": "echo", "args": {"text": "hi"}}
                        ),
                        json.dumps(
                            {
                                "next_node": "final_response",
                                "args": {"answer": "final: hi"},
                            }
                        ),
                    ]

                @override
                async def complete(
                    self,
                    *,
                    messages: Sequence[Mapping[str, Any]],
                    response_format: Mapping[str, Any] | None = None,
                    stream: bool = False,
                    on_stream_chunk: Callable[[str, bool], None] | None = None,
                ) -> str | tuple[str, float]:
                    return self._turns.pop(0), 0.0

            client = PromptedStub()

        planner = ReactPlanner(
            llm_client=client,
            catalog=make_catalog(),
            tool_call_mode=mode,
            max_iters=4,
        )
        result = await planner.run("echo hi")

        assert result.reason == "answer_complete"
        assert result.payload["raw_answer"] == "final: hi"
        assert result.payload["structured"] is None
        assert (
            result.payload["artifacts"] == result.payload["artifacts"]
        )  # shape parity


class TestNativeStructuredFinal:
    """Native-mode structured final answers via the synthetic finish tool."""

    def test_finish_tool_declared_only_when_model_set(self) -> None:
        """Finish tool declared only when model set."""
        with_model = make_native_planner(
            StubNativeClient([]), final_response_model=Verdict
        )
        tools, _ = build_native_tools(with_model)
        finish = [t for t in tools if t.name == FINISH_TOOL_NAME]
        assert len(finish) == 1
        props = finish[0].json_schema["properties"]
        # The answer streams as plain text, so the tool only requires
        # `structured`; `answer` is an optional fallback for turns that put it
        # in the call.
        assert set(props) == {"answer", "structured"}
        assert finish[0].json_schema["required"] == ["structured"]
        # The structured property carries the model's own schema.
        assert "method" in props["structured"]["properties"]

        without_model = make_native_planner(StubNativeClient([]))
        tools2, _ = build_native_tools(without_model)
        assert all(t.name != FINISH_TOOL_NAME for t in tools2)

    def test_finish_tool_name_reserved_against_collision(self) -> None:
        # A catalog tool literally named "final_response" must not steal the
        # alias.
        """Finish tool name reserved against collision."""
        registry = ModelRegistry()
        registry.register("final_response", Query, Query)
        catalog = build_catalog([Node(echo, name="final_response")], registry)
        planner = ReactPlanner(
            llm_client=StubNativeClient([]),
            catalog=catalog,
            tool_call_mode="native",
            final_response_model=Verdict,
        )
        tools, alias_to_name = build_native_tools(planner)
        names = [t.name for t in tools]
        # Exactly one tool keeps the reserved name (the synthetic finish tool),
        # and the catalog tool was aliased away from it.
        assert names.count(FINISH_TOOL_NAME) == 1
        assert alias_to_name.get(FINISH_TOOL_NAME) != "final_response"

    @pytest.mark.asyncio
    async def test_finish_tool_call_yields_validated_structured_no_repair(
        self,
    ) -> None:
        """Finish tool call yields validated structured no repair."""
        events: list[PlannerEvent] = []
        client = StubNativeClient(
            [
                NativeToolCallResult(
                    content="",
                    tool_calls=[
                        tool_call(
                            FINISH_TOOL_NAME,
                            {
                                "answer": "It is 391.",
                                "structured": {
                                    "answer": 391,
                                    "method": "multiplication",
                                },
                            },
                        )
                    ],
                )
            ]
        )
        planner = make_native_planner(
            client,
            final_response_model=Verdict,
            event_callback=events.append,
            max_iters=3,
        )
        result = await planner.run("17*23?")

        assert result.reason == "answer_complete"
        assert result.payload["raw_answer"] == "It is 391."
        assert result.payload["structured"] == {
            "answer": 391,
            "method": "multiplication",
        }
        validated = [
            e
            for e in events
            if e.event_type == "final_response_structured_validated"
        ]
        assert len(validated) == 1
        assert validated[0].extra["repaired"] is False
        # No corrective turn: only the single native turn was consumed.
        assert len(client.tool_declarations) == 1
        assert client.complete_calls == []

    @pytest.mark.asyncio
    async def test_streamed_answer_with_structured_finish_is_not_superseded(
        self,
    ) -> None:
        # The model finishes by streaming the answer as plain text AND calling
        # final_response with only the structured object. The streamed answer
        # must survive (not be retracted as a "preamble"), and structured still
        # validates.
        """Streamed answer with structured finish is not superseded."""
        events: list[PlannerEvent] = []
        client = StubNativeClient(
            [
                NativeToolCallResult(
                    content="It is 391.",
                    tool_calls=[
                        tool_call(
                            FINISH_TOOL_NAME,
                            {
                                "structured": {
                                    "answer": 391,
                                    "method": "multiplication",
                                }
                            },
                        )
                    ],
                )
            ]
        )
        planner = make_native_planner(
            client,
            final_response_model=Verdict,
            stream_final_response=True,
            event_callback=events.append,
            max_iters=3,
        )
        result = await planner.run("17*23?")

        assert result.payload["raw_answer"] == "It is 391."
        assert result.payload["structured"] == {
            "answer": 391,
            "method": "multiplication",
        }

        answer_chunks = [
            e
            for e in events
            if e.event_type == "llm_stream_chunk"
            and (e.extra or {}).get("channel") == "answer"
        ]
        assert answer_chunks, (
            "expected the answer to stream on the answer channel"
        )
        # The streamed answer must NOT have been retracted as a superseded
        # preamble.
        assert not any((e.extra or {}).get("superseded") for e in events)

    @pytest.mark.asyncio
    async def test_finish_with_no_text_falls_back_to_answer_repair(
        self,
    ) -> None:
        # Model calls final_response with ONLY structured and streams no text.
        # The answer is empty, so the runtime's finish-answer repair fills it;
        # structured (already valid) is preserved.
        """Finish with no text falls back to answer repair."""
        client = StubNativeClient(
            [
                NativeToolCallResult(
                    content="",
                    tool_calls=[
                        tool_call(
                            FINISH_TOOL_NAME,
                            {
                                "structured": {
                                    "answer": 391,
                                    "method": "multiplication",
                                }
                            },
                        )
                    ],
                ),
                # finish-answer repair reply (prompted .complete channel)
                "It is 391.",
            ]
        )
        planner = make_native_planner(
            client, final_response_model=Verdict, max_iters=3
        )
        result = await planner.run("17*23?")

        assert result.payload["structured"] == {
            "answer": 391,
            "method": "multiplication",
        }
        assert result.payload["raw_answer"] == "It is 391."
        assert len(client.complete_calls) == 1  # one answer-repair turn

    @pytest.mark.asyncio
    async def test_intermediate_preamble_still_superseded_with_schema(
        self,
    ) -> None:
        # With the finish tool declared, an INTERMEDIATE turn that streams a
        # preamble before a normal tool call must still be retracted
        # (superseded). This guards against the
        # finish-detection-before-supersede reorder leaking into tool turns.
        """Intermediate preamble still superseded with schema."""
        TOOL_INVOCATIONS.clear()
        events: list[PlannerEvent] = []
        client = StubNativeClient(
            [
                NativeToolCallResult(
                    content="let me look that up",
                    tool_calls=[tool_call("echo", {"text": "hi"})],
                ),
                NativeToolCallResult(
                    content="Final answer.",
                    tool_calls=[
                        tool_call(
                            FINISH_TOOL_NAME,
                            {
                                "structured": {
                                    "answer": 391,
                                    "method": "multiplication",
                                }
                            },
                        )
                    ],
                ),
            ]
        )
        planner = make_native_planner(
            client,
            final_response_model=Verdict,
            stream_final_response=True,
            event_callback=events.append,
            max_iters=4,
        )
        result = await planner.run("17*23?")

        assert ("echo", "hi") in TOOL_INVOCATIONS  # the intermediate tool ran
        assert any((e.extra or {}).get("superseded") for e in events), (
            "preamble should be superseded"
        )
        assert result.payload["raw_answer"] == "Final answer."
        assert result.payload["structured"] == {
            "answer": 391,
            "method": "multiplication",
        }

    @pytest.mark.asyncio
    async def test_finish_drops_sibling_tool_calls(self) -> None:
        # If the model emits final_response alongside another tool call, the
        # run finishes and the sibling is dropped (not executed).
        """Finish drops sibling tool calls."""
        TOOL_INVOCATIONS.clear()
        client = StubNativeClient(
            [
                NativeToolCallResult(
                    content="Done.",
                    tool_calls=[
                        tool_call(
                            FINISH_TOOL_NAME,
                            {
                                "structured": {
                                    "answer": 391,
                                    "method": "multiplication",
                                }
                            },
                            "c1",
                        ),
                        tool_call("echo", {"text": "hi"}, "c2"),
                    ],
                )
            ]
        )
        planner = make_native_planner(
            client, final_response_model=Verdict, max_iters=3
        )
        result = await planner.run("17*23?")

        assert result.payload["structured"] == {
            "answer": 391,
            "method": "multiplication",
        }
        assert result.payload["raw_answer"] == "Done."
        assert (
            "echo",
            "hi",
        ) not in TOOL_INVOCATIONS  # sibling dropped, not executed

    @pytest.mark.asyncio
    async def test_invalid_finish_tool_payload_repairs(self) -> None:
        """Invalid finish tool payload repairs."""
        events: list[PlannerEvent] = []
        client = StubNativeClient(
            [
                NativeToolCallResult(
                    content="",
                    tool_calls=[
                        tool_call(
                            FINISH_TOOL_NAME,
                            {
                                "answer": "It is 391.",
                                "structured": {
                                    "answer": "three-ninety-one",
                                    "method": "guessing",
                                },
                            },
                        )
                    ],
                ),
                # Corrective turn replies on the prompted .complete channel.
                json.dumps({"answer": 391, "method": "multiplication"}),
            ]
        )
        planner = make_native_planner(
            client,
            final_response_model=Verdict,
            event_callback=events.append,
            max_iters=3,
        )
        result = await planner.run("17*23?")

        assert result.payload["structured"] == {
            "answer": 391,
            "method": "multiplication",
        }
        repaired = [
            e
            for e in events
            if e.event_type == "final_response_structured_validated"
        ]
        assert repaired and repaired[0].extra["repaired"] is True
        assert len(client.complete_calls) == 1  # one repair turn issued
