# Quickstart

## What it is / when to use it

This page focuses on a runnable "hello world" `ReactPlanner` that matches the current API.

Use it when you want:

- a minimal typed tool + planner you can run in minutes,
- pointers to the next concepts (pause/resume, streaming, A2A).

For deeper explanations, see **[Concepts](concepts.md)**.

## Non-goals / boundaries

- The example uses a scripted LLM stub so it runs offline; swap in a real client for actual use (see **[LLM clients](../planner/llm-clients.md)**).
- It doesn't include production persistence, auth, or deployment.

## Contract surface (what you're exercising)

- Tool definition: `@tool(...)` + `Node(...)` + `ModelRegistry` + `build_catalog(...)`
- Planner lifecycle: `ReactPlanner(llm_client=..., catalog=...)`, `await planner.run(...)`

## 1) Minimal planner

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node, ReactPlanner, build_catalog, tool


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    upper: str


@tool(desc="Upper-case the given text")
async def shout(args: Question, ctx: Any) -> Answer:
    return Answer(upper=args.text.upper())


class ScriptedLLM:
    """Offline stand-in for an LLM: call `shout`, then answer."""

    def __init__(self) -> None:
        self._actions = [
            {"thought": "use the tool", "next_node": "shout", "args": {"text": "hello"}},
            {"thought": "done", "next_node": None, "args": {"raw_answer": "HELLO"}},
        ]

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Any = None,
    ) -> str:
        return json.dumps(self._actions.pop(0))


async def main() -> None:
    registry = ModelRegistry()
    registry.register("shout", Question, Answer)
    catalog = build_catalog([Node(shout, name="shout")], registry)

    planner = ReactPlanner(llm_client=ScriptedLLM(), catalog=catalog)
    result = await planner.run("Shout hello", tool_context={"tenant": "demo"})
    print(result.reason, result.payload["raw_answer"])


if __name__ == "__main__":
    asyncio.run(main())
```

## 2) Bundled examples

```bash
uv run python examples/react_minimal/main.py          # tools + scripted LLM
uv run python examples/react_parallel/main.py         # parallel tool calls + join
uv run python examples/react_pause_resume/main.py     # human-in-the-loop pause/resume
uv run python examples/streaming_llm/stream_answer.py # streamed final answer via PlannerEvent
uv run python examples/a2a_grpc_server/main.py        # serve a planner over A2A gRPC (a2a-grpc extra)
```

Planner docs:

- **[ReactPlanner overview](../planner/overview.md)**
- **[Tooling (ToolNode)](../planner/tooling.md)**
- **[Pause/resume (HITL)](../planner/pause-resume-hitl.md)**

## Operational defaults

- Keep tool signatures `async def fn(args, ctx)` (two positional params).
- Register input/output models for every tool so arguments and results are validated.
- Prefer bounded outputs; use artifacts for large blobs in production.

## Failure modes & recovery

- **`TypeError: ... unexpected keyword argument 'stream'`**: custom LLM clients must accept `stream` and `on_stream_chunk` keyword arguments.
- **Validation errors**: ensure `ModelRegistry` includes entries for every tool name in the catalog.

## Observability

Pass `event_callback=` to `ReactPlanner` to receive `PlannerEvent`s; see **[Planner observability](../planner/observability.md)**.

## Security / multi-tenancy notes

- Put tenant and session identifiers in `tool_context`, not in the query.
- Don't embed secrets in tool outputs if you persist trajectories or logs.

## Troubleshooting checklist

- **Planner examples fail to import**: verify `uv` and the required extras are installed (`penguiflow-lite[planner]`).
