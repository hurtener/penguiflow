"""Minimal flow showing scope classifier guardrail wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel

from penguiflow.catalog import build_catalog
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.guardrails import AsyncRuleEvaluator, GuardrailGateway, RuleRegistry
from penguiflow.registry import ModelRegistry
from penguiflow.steering import InMemoryGuardInbox

from .rule import ScopeClassifierRule


class _StubClient:
    async def complete(self, *, messages, response_format=None, stream=False, on_stream_chunk=None):
        _ = messages, response_format, stream, on_stream_chunk
        return '{"thought":"done","next_node":"final_response","args":{"answer":"ok"}}', 0.0


class _Args(BaseModel):
    ok: bool = True


class _Result(BaseModel):
    ok: bool


async def _noop_tool(args: _Args, ctx: object) -> _Result:
    _ = ctx
    return _Result(ok=args.ok)


async def main() -> None:
    registry = ModelRegistry()
    registry.register("noop", _Args, _Result)
    catalog = build_catalog([Node(_noop_tool, name="noop")], registry)

    rule_registry = RuleRegistry()
    rule_registry.register(
        ScopeClassifierRule(model_path=Path("examples/guardrails/scope_classifier/scope_model.joblib"))
    )

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

    result = await planner.run("Show revenue by region")
    print(result.reason)


if __name__ == "__main__":
    asyncio.run(main())
