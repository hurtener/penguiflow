"""Tests for guardrails integration."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog
from penguiflow.node import Node
from penguiflow.planner import PlannerFinish, ReactPlanner
from penguiflow.planner.guardrails import (
    AsyncRuleEvaluator,
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvent,
    GuardrailGateway,
    GuardrailSeverity,
    RuleCost,
    RuleRegistry,
    SecretRedactionRule,
    ToolAllowlistRule,
)
from penguiflow.planner.guardrails.protocols import GuardrailRule
from penguiflow.planner.memory import MemoryKey, ShortTermMemoryConfig
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry
from penguiflow.steering import InMemoryGuardInbox

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class _StubClient(JSONLLMClient):
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = [json.dumps(item) for item in responses]

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        if not self._responses:
            msg = "No stub responses left"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


class ToolArgs(BaseModel):
    """Arguments of the demo tools."""

    value: str


class ToolOut(BaseModel):
    """Output of the demo tools."""

    result: str


async def demo_tool(args: ToolArgs, _ctx: object) -> ToolOut:
    """Echo the input as the output."""
    return ToolOut(result=f"echo:{args.value}")


async def secret_tool(_args: ToolArgs, _ctx: object) -> ToolOut:
    """Return a payload that holds a secret."""
    return ToolOut(result="sk-abc12345678901234567890")


class _ConversationHistoryRule(GuardrailRule):
    rule_id = "history"
    version = "1"
    supports_event_types = frozenset({"llm_before"})
    enabled = True
    cost = RuleCost.FAST
    severity = GuardrailSeverity.LOW

    def __init__(self, *, expected_user: str) -> None:
        self._expected_user = expected_user

    @override
    async def evaluate(
        self, event: GuardrailEvent, context_snapshot: object
    ) -> GuardrailDecision | None:
        history = event.payload.get("conversation_history")
        assert isinstance(history, list)
        assert history, (
            "expected conversation_history to be populated from "
            "short-term memory"
        )
        assert history[-1]["user"] == self._expected_user
        return GuardrailDecision(
            action=GuardrailAction.ALLOW, rule_id=self.rule_id, reason="ok"
        )


@pytest.mark.asyncio
async def test_guardrail_payload_has_conversation_history() -> None:
    """Guardrail payload includes conversation history from memory."""
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
                {
                    "thought": "answer",
                    "next_node": "final_response",
                    "args": {"answer": "one"},
                },
                {
                    "thought": "answer",
                    "next_node": "final_response",
                    "args": {"answer": "two"},
                },
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
    """Guardrail tool allowlist stop."""
    registry = ModelRegistry()
    registry.register("demo", ToolArgs, ToolOut)
    catalog = build_catalog([Node(demo_tool, name="demo")], registry)

    guard_registry = RuleRegistry()
    guard_registry.register(
        ToolAllowlistRule(denied_tools=frozenset({"demo"}))
    )
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
    """Guardrail secret redaction on tool result."""
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
    assert isinstance(result, PlannerFinish)
    steps = result.metadata.get("steps", [])
    assert steps
    observation = steps[0].get("observation") or {}
    assert "OPENAI_KEY" in str(observation.get("result"))
