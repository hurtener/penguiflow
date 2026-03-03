# Tool design (planner tools)

## What this is / when to use it

This page explains how to design tools for `ReactPlanner` so they are:

- easy for the LLM to call correctly,
- safe to run in production,
- observable and debuggable.

## Non-goals / boundaries

- This is not a full ToolNode guide (see **[Tooling](tooling.md)**).
- This is not a style guide for your application domain; it focuses on PenguiFlow contracts.

## Contract surface

Planner tools are represented as `NodeSpec` records built from:

- a `Node` (wrapping an async function)
- Pydantic args and output models registered in `ModelRegistry`
- optional metadata from `@tool(...)` (tags, side effects, examples, safety notes)

### `@tool` metadata

Use the `penguiflow.catalog.tool` decorator to annotate tools:

- `desc`: one-sentence intent
- `side_effects`: `"pure" | "read" | "write" | "external" | "stateful"`
- `tags`: categorization (routing and allowlisting)
- `auth_scopes`: required scopes (for OAuth-aware toolsets)
- `safety_notes`: “foot-gun” warnings shown to the model
- `examples`: input examples to improve arg quality

## Runnable example: a typed, cataloged tool

```python
from __future__ import annotations

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import ToolContext


class SearchArgs(BaseModel):
    query: str


class SearchOut(BaseModel):
    titles: list[str]


@tool(
    desc="Search a private knowledge base by keyword",
    side_effects="read",
    tags=["kb", "search"],
    safety_notes="Do not return raw secrets; summarize results.",
    examples={"args": {"query": "incident runbook"}, "description": "Typical query"},
)
async def kb_search(args: SearchArgs, ctx: ToolContext) -> SearchOut:
    del ctx
    return SearchOut(titles=[f"Result for: {args.query}"])


registry = ModelRegistry()
registry.register("kb_search", SearchArgs, SearchOut)
catalog = build_catalog([Node(kb_search, name="kb_search")], registry)
```

## Operational defaults

### Prefer “small args, small outputs”

Small schemas produce fewer LLM arg errors and reduce prompt cost.

If a tool can return large/binary payloads:

- store them in `ctx.artifacts` and return a compact reference (or a summarized view),
- mark large fields as artifacts when applicable (see tools docs).

### Make tools retry-safe

Planner retries and parallelism are easier if tools are idempotent.

Guidelines:

- `side_effects="pure"` or `"read"` tools should be safe to retry.
- `"write"` tools should accept an idempotency key (commonly `trace_id` or a request id).
- For irreversible operations, require HITL approval (pause) before committing.

### Use `ToolContext` correctly

`ToolContext` provides:

- `llm_context`: LLM-visible context (read-only mapping)
- `tool_context`: tool-only context (secrets, clients, loggers)
- `artifacts`: scoped artifact facade (`ScopedArtifacts`) — use `upload()`/`download()`/`list()` for large/binary payloads with automatic scope injection
- `pause(...)`: pause execution for approvals/OAuth
- `emit_chunk(...)`: stream partial output

## Failure modes & recovery

- **ValidationError on args**: improve examples, simplify args model, enable arg-fill.
- **Tool raises exceptions**: the planner records a structured error and may re-plan or finish depending on budget.
- **Large tool outputs**: may be clamped/truncated by planner guardrails; use artifacts instead.

## Observability

- Use `event_callback` to capture planner tool-call events.
- Record tool latency, error class, and retry attempts.
- Avoid logging raw payloads; log references (artifact ids) or summaries.

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Never read secrets from `llm_context`.
- Enforce per-tenant tool visibility with a `ToolPolicy` or `tool_visibility` policy.
- If tool outputs contain PII, treat them as artifacts and only expose redacted summaries to the LLM.

## Troubleshooting checklist

- **Tool not chosen by the model**: add `tags`, improve `desc`, provide examples, and ensure it appears in the catalog shown to the LLM.
- **Args frequently invalid**: reduce schema surface area, add examples, and confirm JSON schema mode is enabled.
- **Tool outputs overflow context**: enforce truncation or move payloads to artifacts.

