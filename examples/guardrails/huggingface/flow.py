"""Minimal flow showing HuggingFace prompt-injection guardrail wiring."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.guardrails import (
    AsyncRuleEvaluator,
    GuardrailGateway,
    RuleRegistry,
)
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry
from penguiflow.steering import InMemoryGuardInbox

from .rule import HuggingFaceJailbreakRule

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class _StubClient(JSONLLMClient):
    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]:
        return (
            '{"thought":"done","next_node":"final_response","args":{"answer":"ok"}}',
            0.0,
        )


class _Args(BaseModel):
    ok: bool = True


class _Result(BaseModel):
    ok: bool


async def _noop_tool(args: _Args, _ctx: object) -> _Result:
    return _Result(ok=args.ok)


async def main() -> None:
    """Run the example."""
    registry = ModelRegistry()
    registry.register("noop", _Args, _Result)
    catalog = build_catalog([Node(_noop_tool, name="noop")], registry)

    rule_registry = RuleRegistry()
    rule_registry.register(HuggingFaceJailbreakRule())

    gateway = GuardrailGateway(
        registry=rule_registry,
        guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(rule_registry)),
    )

    planner = ReactPlanner(
        llm_client=_StubClient(),
        catalog=catalog,
        max_iters=1,
        guardrail_gateway=gateway,
    )

    result = await planner.run("Hello")
    sys.stdout.write(f"{result.reason}\n")


if __name__ == "__main__":
    asyncio.run(main())
