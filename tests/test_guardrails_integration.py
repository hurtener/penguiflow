from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from penguiflow.catalog import build_catalog
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.guardrails import (
    AsyncRuleEvaluator,
    GatewayConfig,
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvent,
    GuardrailGateway,
    GuardrailSeverity,
    PauseSpec,
    RiskRoutingDecision,
    RuleCost,
    RuleRegistry,
    SecretRedactionRule,
    ToolAllowlistRule,
)
from penguiflow.planner.memory import MemoryKey, ShortTermMemoryConfig
from penguiflow.planner.models import PlannerEvent, PlannerFinish, PlannerPause
from penguiflow.registry import ModelRegistry
from penguiflow.steering import InMemoryGuardInbox


class _StubClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = [json.dumps(item) for item in responses]

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, object] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del messages, response_format, stream, on_stream_chunk
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0), 0.0


class ToolArgs(BaseModel):
    value: str


class ToolOut(BaseModel):
    result: str


async def demo_tool(args: ToolArgs, ctx: object) -> ToolOut:
    del ctx
    return ToolOut(result=f"echo:{args.value}")


async def secret_tool(args: ToolArgs, ctx: object) -> ToolOut:
    del ctx
    return ToolOut(result="sk-abc12345678901234567890")


class _ConversationHistoryRule:
    rule_id = "history"
    version = "1"
    supports_event_types = frozenset({"llm_before"})
    enabled = True
    cost = RuleCost.FAST
    severity = GuardrailSeverity.LOW

    def __init__(self, *, expected_user: str) -> None:
        self._expected_user = expected_user

    async def evaluate(self, event: GuardrailEvent, context_snapshot: object) -> GuardrailDecision | None:
        del context_snapshot
        history = event.payload.get("conversation_history")
        assert isinstance(history, list)
        assert history, "expected conversation_history to be populated from short-term memory"
        assert history[-1]["user"] == self._expected_user
        return GuardrailDecision(action=GuardrailAction.ALLOW, rule_id=self.rule_id, reason="ok")


@pytest.mark.asyncio
async def test_guardrail_payload_includes_conversation_history_from_memory() -> None:
    registry = ModelRegistry()
    catalog = build_catalog([], registry)

    guard_registry = RuleRegistry()
    guard_registry.register(_ConversationHistoryRule(expected_user="first"))
    gateway = GuardrailGateway(
        registry=guard_registry,
        guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(guard_registry)),
    )

    planner = ReactPlanner(
        llm_client=_StubClient(
            [
                {"thought": "answer", "next_node": "final_response", "args": {"answer": "one"}},
                {"thought": "answer", "next_node": "final_response", "args": {"answer": "two"}},
            ]
        ),
        catalog=catalog,
        max_iters=1,
        short_term_memory=ShortTermMemoryConfig(strategy="truncation"),
        guardrail_gateway=gateway,
        guardrail_conversation_history_turns=2,
    )

    key = MemoryKey(tenant_id="t", user_id="u", session_id="s")
    await planner.run("first", memory_key=key)
    await planner.run("second", memory_key=key)


@pytest.mark.asyncio
async def test_guardrail_tool_allowlist_stop() -> None:
    registry = ModelRegistry()
    registry.register("demo", ToolArgs, ToolOut)
    catalog = build_catalog([Node(demo_tool, name="demo")], registry)

    guard_registry = RuleRegistry()
    guard_registry.register(ToolAllowlistRule(denied_tools=frozenset({"demo"})))
    gateway = GuardrailGateway(
        registry=guard_registry,
        guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(guard_registry)),
    )

    planner = ReactPlanner(
        llm_client=_StubClient(
            [
                {
                    "thought": "call demo",
                    "next_node": "demo",
                    "args": {"value": "hi"},
                }
            ]
        ),
        catalog=catalog,
        max_iters=1,
        guardrail_gateway=gateway,
    )

    result = await planner.run("hi")

    assert result.reason == "no_path"
    assert "guardrail" in result.metadata
    assert result.metadata["guardrail"]["rule_id"] == "tool-allowlist"
    assert "unable" in str(result.payload.get("raw_answer")).lower()


@pytest.mark.asyncio
async def test_guardrail_secret_redaction_on_tool_result() -> None:
    registry = ModelRegistry()
    registry.register("secret", ToolArgs, ToolOut)
    catalog = build_catalog([Node(secret_tool, name="secret")], registry)

    guard_registry = RuleRegistry()
    guard_registry.register(SecretRedactionRule())
    gateway = GuardrailGateway(
        registry=guard_registry,
        guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(guard_registry)),
    )

    planner = ReactPlanner(
        llm_client=_StubClient(
            [
                {
                    "thought": "call secret",
                    "next_node": "secret",
                    "args": {"value": "hi"},
                }
            ]
        ),
        catalog=catalog,
        max_iters=1,
        guardrail_gateway=gateway,
    )

    result = await planner.run("hi")
    steps = result.metadata.get("steps", [])
    assert steps
    observation = steps[0].get("observation") or {}
    assert "OPENAI_KEY" in str(observation.get("result"))


# ---------------------------------------------------------------------------
# Issue 128: guardrail PAUSE (bug 6: tool events; item 8: LLM events)
# ---------------------------------------------------------------------------


class _PauseRule:
    """FAST rule that asks for approval on one event type, a limited number of times."""

    version = "1"
    enabled = True
    cost = RuleCost.FAST
    severity = GuardrailSeverity.MEDIUM

    def __init__(self, event_type: str, *, times: int = 1_000, prompt: str = "Approve?") -> None:
        self.rule_id = "pause-rule"
        self.supports_event_types = frozenset({event_type})
        self._remaining = times
        self._prompt = prompt

    async def evaluate(self, event: GuardrailEvent, context_snapshot: object) -> GuardrailDecision | None:
        del event, context_snapshot
        if self._remaining <= 0:
            return None
        self._remaining -= 1
        return GuardrailDecision(
            action=GuardrailAction.PAUSE,
            rule_id=self.rule_id,
            reason="needs approval",
            pause=PauseSpec(prompt=self._prompt, approver_roles=("ops",)),
        )


def _gateway(*rules: object, **gateway_kwargs: object) -> GuardrailGateway:
    guard_registry = RuleRegistry()
    for rule in rules:
        guard_registry.register(rule)  # type: ignore[arg-type]
    gateway_kwargs.setdefault("guard_inbox", InMemoryGuardInbox(AsyncRuleEvaluator(guard_registry)))
    gateway_kwargs.setdefault("config", GatewayConfig(mode="enforce", async_enabled=False))
    return GuardrailGateway(registry=guard_registry, **gateway_kwargs)  # type: ignore[arg-type]


class _CountingTool:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, args: ToolArgs, ctx: object) -> ToolOut:
        del ctx
        self.calls += 1
        return ToolOut(result=f"echo:{args.value}")


def _planner_with_tool(
    tool_fn: _CountingTool,
    responses: list[dict[str, object]],
    gateway: GuardrailGateway,
    **kwargs: object,
) -> tuple[ReactPlanner, _StubClient]:
    registry = ModelRegistry()
    registry.register("demo", ToolArgs, ToolOut)

    async def demo(args: ToolArgs, ctx: object) -> ToolOut:
        return await tool_fn(args, ctx)

    client = _StubClient(responses)
    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(demo, name="demo")], registry),
        guardrail_gateway=gateway,
        **kwargs,  # type: ignore[arg-type]
    )
    return planner, client


_CALL_DEMO = {"thought": "call demo", "next_node": "demo", "args": {"value": "hi"}}
_FINISH = {"thought": "done", "next_node": "final_response", "args": {"answer": "ok"}}


@pytest.mark.asyncio
async def test_guardrail_pause_on_tool_call_start_returns_planner_pause() -> None:
    tool_fn = _CountingTool()
    planner, _ = _planner_with_tool(
        tool_fn, [_CALL_DEMO, _FINISH], _gateway(_PauseRule("tool_call_start", times=1))
    )

    result = await planner.run("hi")

    assert isinstance(result, PlannerPause)
    assert result.reason == "approval_required"
    guardrail = result.payload["guardrail"]
    assert guardrail["action"] == "PAUSE"
    assert guardrail["rule_id"] == "pause-rule"
    assert guardrail["pause"]["prompt"] == "Approve?"
    assert guardrail["pause"]["approver_roles"] == ["ops"]
    assert tool_fn.calls == 0

    resumed = await planner.resume(result.resume_token, user_input="approved")
    assert isinstance(resumed, PlannerFinish)


@pytest.mark.asyncio
async def test_guardrail_pause_on_tool_call_result_returns_planner_pause() -> None:
    tool_fn = _CountingTool()
    planner, _ = _planner_with_tool(tool_fn, [_CALL_DEMO], _gateway(_PauseRule("tool_call_result")))

    result = await planner.run("hi")

    assert isinstance(result, PlannerPause)
    assert result.reason == "approval_required"
    assert result.payload["guardrail"]["rule_id"] == "pause-rule"
    assert tool_fn.calls == 1


@pytest.mark.asyncio
async def test_guardrail_pause_on_tool_call_fails_closed_when_pause_disabled() -> None:
    tool_fn = _CountingTool()
    events: list[PlannerEvent] = []
    planner, _ = _planner_with_tool(
        tool_fn,
        [_CALL_DEMO, _FINISH],
        _gateway(_PauseRule("tool_call_start")),
        pause_enabled=False,
        event_callback=events.append,
    )

    result = await planner.run("hi")

    assert isinstance(result, PlannerFinish)
    assert tool_fn.calls == 0
    tool_results = [e.extra["result_json"] for e in events if e.event_type == "tool_call_result"]
    assert any("guardrail_pause_unavailable:pause-rule" in payload for payload in tool_results)


@pytest.mark.asyncio
async def test_guardrail_pause_on_llm_before_returns_planner_pause() -> None:
    tool_fn = _CountingTool()
    planner, client = _planner_with_tool(tool_fn, [_FINISH], _gateway(_PauseRule("llm_before", times=1)))

    result = await planner.run("hi")

    assert isinstance(result, PlannerPause)
    assert result.reason == "approval_required"
    assert result.payload["guardrail"]["pause"]["prompt"] == "Approve?"
    assert client._responses, "the LLM must not be called while approval is pending"

    resumed = await planner.resume(result.resume_token, user_input="approved")
    assert isinstance(resumed, PlannerFinish)
    assert not client._responses


@pytest.mark.asyncio
async def test_guardrail_pause_on_llm_before_fails_closed_when_pause_disabled() -> None:
    tool_fn = _CountingTool()
    planner, client = _planner_with_tool(
        tool_fn, [_FINISH], _gateway(_PauseRule("llm_before")), pause_enabled=False
    )

    result = await planner.run("hi")

    assert isinstance(result, PlannerFinish)
    assert "unable" in str(result.payload.get("raw_answer")).lower()
    assert client._responses, "the LLM must not be called when the request needs approval"


@pytest.mark.asyncio
async def test_guardrail_pause_from_stream_chunk_returns_planner_pause() -> None:
    tool_fn = _CountingTool()
    registry = ModelRegistry()
    registry.register("demo", ToolArgs, ToolOut)

    async def demo(args: ToolArgs, ctx: object) -> ToolOut:
        return await tool_fn(args, ctx)

    holder: dict[str, ReactPlanner] = {}

    class _StreamPausingClient:
        """Simulates the stream guardrail handler deciding PAUSE while the LLM streams."""

        async def complete(self, **_: object) -> tuple[str, float]:
            holder["planner"]._guardrail_stream_decision = GuardrailDecision(
                action=GuardrailAction.PAUSE,
                rule_id="stream-rule",
                reason="streamed content needs review",
            )
            return json.dumps(_CALL_DEMO), 0.0

    planner = ReactPlanner(
        llm_client=_StreamPausingClient(),
        catalog=build_catalog([Node(demo, name="demo")], registry),
        guardrail_gateway=_gateway(),
    )
    holder["planner"] = planner

    result = await planner.run("hi")

    assert isinstance(result, PlannerPause)
    assert result.reason == "approval_required"
    assert result.payload["guardrail"]["rule_id"] == "stream-rule"
    assert tool_fn.calls == 0


@pytest.mark.asyncio
async def test_risk_router_timeout_on_llm_before_pauses() -> None:
    class _AlwaysPauseOnTimeout:
        def route(self, event: GuardrailEvent, context_snapshot: object) -> RiskRoutingDecision:
            del context_snapshot
            if event.event_type == "llm_before":
                return RiskRoutingDecision(async_wait_ms=1.0, on_timeout="pause")
            return RiskRoutingDecision()

    class _SilentInbox:
        async def submit(self, event: object) -> str:
            del event
            return "corr-1"

        async def await_response(self, correlation_id: str, timeout_s: float) -> object:
            del correlation_id, timeout_s
            raise TimeoutError

    tool_fn = _CountingTool()
    gateway = _gateway(
        guard_inbox=_SilentInbox(),
        config=GatewayConfig(mode="enforce", async_enabled=True),
        risk_router=_AlwaysPauseOnTimeout(),
    )
    planner, client = _planner_with_tool(tool_fn, [_FINISH], gateway)

    result = await planner.run("hi")

    assert isinstance(result, PlannerPause)
    assert result.payload["guardrail"]["rule_id"] == "__timeout__"
    assert result.payload["guardrail"]["pause"]["prompt"] == "Safety check timed out"
    assert client._responses
