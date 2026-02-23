# Parallel & joins (planner)

## What this is / when to use it

Use planner-level parallelism when the task decomposes into independent subqueries that benefit from concurrency:

- multiple independent sources (GitHub + DB + docs)
- multiple independent queries to the same source (with rate limits)
- “gather evidence” before synthesis

## Non-goals / boundaries

- This is not runtime-level fan-out/fan-in (see `docs/core/concurrency.md`).
- Parallel plans do not guarantee ordering across branches.

## Contract surface

Parallel plans are expressed via `PlannerAction(next_node="parallel")`.

See **[Actions & schema](actions-and-schema.md)** for the JSON shape and join injection sources.

### Join behavior (must-know)

From the planner’s parallel executor:

- branch tools run concurrently
- if any branch fails, join is **skipped** (`reason="branch_failures"`)
- join can also pause; in that case the whole planner pauses

## Operational defaults

### Concurrency limits (3 layers)

PenguiFlow uses three relevant knobs:

1. **Planner safety limit**: `absolute_max_parallel` (default 50)
2. **Per-run hints**: `planning_hints.max_parallel` (optional)
3. **Per-source limit** (ToolNode): `ExternalToolConfig.max_concurrency` (default 10)

Recommended starting points:

- External SaaS APIs: ToolNode max_concurrency 3–5
- Internal services: 10–50 depending on rate limits and capacity
- Planner absolute_max_parallel: keep 20–50 unless you have strong infra support

## Failure modes & recovery

### Join skipped unexpectedly

Expected: join is skipped if any branch produced an error.

Actions:

- inspect branch failures (`$failures` / `$branches` in the parallel observation)
- reduce tool surface or add retries to flaky tools
- use a join tool only when branches are “must succeed”

### Invalid join injection source

If `join.inject` references an unknown source, the planner records an error and the parallel step is treated as invalid.

Actions:

- use only: `$results`, `$branches`, `$failures`, `$success_count`, `$failure_count`, `$expect`
- verify the join tool args model matches injected shapes

### Join validation error

If the join tool args model rejects the injected payload, the join fails validation.

Actions:

- explicitly map the injected data to arg fields you own
- keep join args models permissive enough (e.g. `list[dict[str, Any]]`) if needed

## Observability

- Emit per-branch tool latency and error rates.
- Alert on increasing `branch_failures` or join validation failures (usually indicates drift in join tool schema).
- Record `absolute_max_parallel` and ToolNode `max_concurrency` in config snapshots.

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Parallelism can amplify impact: rate-limit and allowlist external tools.
- Avoid running “write” tools in parallel unless idempotency is guaranteed.

## Troubleshooting checklist

- **Parallel feels slower than sequential**: you may be bottlenecked by ToolNode concurrency or external rate limits.
- **Planner returns no_path**: reduce parallelism, improve tool descriptions/examples, and ensure tool visibility is correct.

## Runnable example: explicit join injection

This example scripts the planner action to run two tools in parallel and then execute a join tool.

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import PlannerFinish, ReactPlanner, ToolContext
from penguiflow.planner.models import JSONLLMClient


class AArgs(BaseModel):
    pass


class AOut(BaseModel):
    value: str


class BArgs(BaseModel):
    pass


class BOut(BaseModel):
    value: str


class JoinArgs(BaseModel):
    results: list[dict[str, Any]]
    expect: int


class JoinOut(BaseModel):
    combined: list[str]


@tool(desc="Return A", side_effects="pure")
async def tool_a(args: AArgs, ctx: ToolContext) -> AOut:
    del args, ctx
    return AOut(value="a")


@tool(desc="Return B", side_effects="pure")
async def tool_b(args: BArgs, ctx: ToolContext) -> BOut:
    del args, ctx
    return BOut(value="b")


@tool(desc="Join results", side_effects="pure")
async def join_tool(args: JoinArgs, ctx: ToolContext) -> JoinOut:
    del ctx
    return JoinOut(combined=[str(r.get("value")) for r in args.results])


class ScriptedClient(JSONLLMClient):
    def __init__(self) -> None:
        self._step = 0

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        del messages, response_format, stream, on_stream_chunk
        self._step += 1

        if self._step == 1:
            return json.dumps(
                {
                    "next_node": "parallel",
                    "args": {
                        "steps": [{"node": "tool_a", "args": {}}, {"node": "tool_b", "args": {}}],
                        "join": {
                            "node": "join_tool",
                            "args": {},
                            "inject": {"results": "$results", "expect": "$expect"},
                        },
                    },
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {"next_node": "final_response", "args": {"answer": "done"}},
            ensure_ascii=False,
        )


async def main() -> None:
    registry = ModelRegistry()
    registry.register("tool_a", AArgs, AOut)
    registry.register("tool_b", BArgs, BOut)
    registry.register("join_tool", JoinArgs, JoinOut)

    nodes = [
        Node(tool_a, name="tool_a"),
        Node(tool_b, name="tool_b"),
        Node(join_tool, name="join_tool"),
    ]
    catalog = build_catalog(nodes, registry)

    planner = ReactPlanner(llm_client=ScriptedClient(), catalog=catalog)
    result = await planner.run("demo", tool_context={"session_id": "demo"})
    assert isinstance(result, PlannerFinish)
    print(result.reason, getattr(result.payload, "raw_answer", result.payload))


if __name__ == "__main__":
    asyncio.run(main())
```
