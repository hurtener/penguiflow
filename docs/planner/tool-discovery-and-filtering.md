# Tool discovery & filtering (tool_search, visibility, activation)

## What it is / when to use it

ReactPlanner supports **enterprise-grade tool control** along three axes:

1) **Filtering**: decide which tools exist for a request (policy + visibility).
2) **Discovery**: let the model find tools by capability (`tool_search` + `tool_get`).
3) **Deferred activation**: keep most tools out of the prompt until needed (loading mode `deferred`).

Use this page when:

- your catalog is large (dozens to hundreds of tools),
- you need per-tenant/per-user tool allowlists,
- you want the LLM to “discover” tools safely without showing everything at once,
- you are debugging “tool not found” or unexpected tool availability.

## Non-goals / boundaries

- This page does not replace ToolNode runbooks (see **[Tooling](tooling.md)**).
- This page is not an authorization system: you must still authenticate/authorize tool usage at the service boundary.
- Tool discovery does not guarantee a model will choose the best tool; it only provides a **safe surface** to query schemas and activate tools.

## Contract surface

### 1) Static filtering: `ToolPolicy` (planner construction-time)

`ToolPolicy` is a coarse, static allow/deny layer applied when the planner is initialized:

- `allowed_tools: set[str] | None` (allowlist; `None` means “no allowlist”)
- `denied_tools: set[str]` (denylist wins)
- `require_tags: set[str]` (tool must include all required tags)

This is enforced once in `ReactPlanner(...)` construction. If you need per-request variation, use `tool_visibility` (below).

### 2) Dynamic filtering: `ToolVisibilityPolicy` (per-run/per-resume)

`ToolVisibilityPolicy` is an opt-in protocol that filters tools for a specific call:

- `visible_tools(specs, tool_context) -> Sequence[NodeSpec]`

Use it for:

- per-tenant enablement of tools,
- feature flags per user/session,
- emergency kill-switches (disable a tool live without rebuilding the planner).

Important behavior:

- visibility affects what the LLM can see **and** what `tool_search` / `tool_get` can return.
- a tool that is not visible cannot be activated via deferred activation (activation is denied).

### 3) Discovery tools: `tool_search` and `tool_get`

When `ToolSearchConfig.enabled=True`, the planner injects two always-visible tools:

- `tool_search(query, search_type, limit, include_always_loaded)`
- `tool_get(names=[...], include_schemas=True, include_examples=True)`

The typical workflow:

1. LLM calls `tool_search` with a natural language capability query.
2. LLM optionally calls `tool_get` to fetch schemas/examples for 1–3 candidate tools.
3. LLM calls the chosen tool by name.

### 4) Deferred activation: `ToolLoadingMode.DEFERRED`

Tools can have:

- `loading_mode="always"`: visible in the prompt immediately
- `loading_mode="deferred"`: hidden unless activated

When tool search is enabled, the planner’s initial visible catalog is:

- all `always` tools, plus
- tools matching `ToolSearchConfig.always_loaded_patterns`

When the model tries to call a deferred tool by name:

- if it’s allowed by visibility, the runtime activates it and refreshes the visible catalog
- then the tool executes normally

### Tool discovery configuration: `ToolSearchConfig`

Key knobs:

- `enabled`: master switch
- `default_loading_mode`: default loading mode for tools built via `build_catalog(...)` (often set to `deferred`)
- `always_loaded_patterns`: globs for tools that must remain visible (e.g., `tool_search`, `tool_get`, `tasks.*`, `finish`)
- `activation_scope`:
  - `"run"`: activations live only for the current run/resume token
  - `"session"`: activations persist for `tool_context["session_id"]`
- cache/index:
  - `cache_dir`, `enable_incremental_index`, `rebuild_cache_on_init`, `max_search_results`
- optional prompt aids:
  - `hints.*`: auto-suggest a small shortlist each turn
  - `directory.*`: render a grouped “tool directory” block (namespaces/tags)

## Operational defaults (recommended)

For large catalogs:

- Enable tool discovery: `ToolSearchConfig(enabled=True, default_loading_mode="deferred")`
- Keep an explicit always-visible core set:
  - `tool_search`, `tool_get`, `finish`, `tasks.*` (if tasks are enabled)
- Use `activation_scope="session"` for interactive chat sessions (requires `tool_context["session_id"]`)
- Use `ToolVisibilityPolicy` for per-tenant/per-user filtering; keep `ToolPolicy` for global guardrails.

For small catalogs (< ~20 tools):

- keep everything `always` and disable tool search (lower complexity).

## Failure modes & recovery

### `tool_search is not configured`

**Likely cause**

- `ToolSearchConfig.enabled=False`, or the cache was not created

**Fix**

- enable tool discovery on the planner and ensure `tool_search` is included in the catalog.

### “Tool not found” after tool_search suggested it

**Likely causes**

- tool is filtered by `tool_visibility` for this request
- tool activation denied due to visibility policy
- tool name is an alias and you called the alias when only the real name is allowed

**Fix**

- confirm allowed tool names for the call (visibility and policy)
- prefer calling `tool_get` before execution to confirm the exact tool name

### FTS not available / search quality is poor

The search cache uses SQLite FTS5 when available. If FTS5 cannot be created, `tool_search` falls back to regex or exact matching.

**Fix**

- ensure your Python/SQLite build supports FTS5, or
- tune `preferred_namespaces`, tool descriptions, and tags for better regex/exact matching.

### Prompt is still huge

**Likely cause**

- too many tools are `always` (or match `always_loaded_patterns`)

**Fix**

- move tools to `deferred`
- keep only a minimal always-visible core set
- enable `tool_search.hints` instead of exposing the full directory

## Observability

Recommended events/logs to record:

- initialization:
  - `tool_search_cache_ready` (tool_count, fts_available, visible_tool_count)
  - `planner_tool_policy_filtered` (removed tools)
- discovery:
  - `tool_search_query` (requested/effective search type, results count)
  - `tool_get`
  - `tool_hints_generated`, `tool_directory_rendered` (if enabled)
- activation:
  - `tool_activated` (tool_name, activation_scope)
  - `tool_activation_denied` (reason=visibility_policy)

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Never rely on prompt text to restrict tools. Enforce it in `ToolPolicy` / `ToolVisibilityPolicy`.
- Ensure `tool_search` and `tool_get` are filtered by the same visibility rules as direct tool calls (ReactPlanner does this by using allowed-name sets).
- Treat tool schemas/examples as sensitive: they can leak capabilities. Only return schemas for tools the caller is allowed to use.

## Runnable example: deferred activation with tool_search

This example demonstrates an end-to-end flow:

- tools are deferred by default
- the model uses `tool_search` to find a tool name
- the runtime activates the deferred tool on first call

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import ToolLoadingMode, build_catalog, tool
from penguiflow.planner import PlannerFinish, ReactPlanner, ToolContext
from penguiflow.planner.models import JSONLLMClient, ToolSearchConfig


class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    text: str


@tool(desc="Echo a string back to you.", side_effects="pure", loading_mode=ToolLoadingMode.DEFERRED)
async def echo(args: EchoArgs, ctx: ToolContext) -> EchoOut:
    del ctx
    return EchoOut(text=args.text)


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
            return json.dumps({"next_node": "tool_search", "args": {"query": "echo", "limit": 3}}, ensure_ascii=False)
        if self._step == 2:
            # Note: echo is deferred and not initially visible; the runtime activates it on first call.
            return json.dumps({"next_node": "echo", "args": {"text": "hello"}}, ensure_ascii=False)
        return json.dumps({"next_node": "final_response", "args": {"answer": "done"}}, ensure_ascii=False)


async def main() -> None:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)

    planner = ReactPlanner(
        llm_client=ScriptedClient(),
        catalog=catalog,
        tool_search=ToolSearchConfig(
            enabled=True,
            default_loading_mode=ToolLoadingMode.DEFERRED,
            activation_scope="run",
        ),
    )

    result = await planner.run("demo", tool_context={"session_id": "demo"})
    assert isinstance(result, PlannerFinish)
    print(result.reason)


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- Are you using `ToolPolicy` for global allow/deny and `tool_visibility` for per-request filtering?
- If `activation_scope="session"`, are you passing `tool_context["session_id"]`?
- Are most tools `deferred`, with only a minimal always-visible set?
- Are you recording `tool_search_query` and `tool_activated` events?

