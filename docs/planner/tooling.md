# Tooling (ToolNode)

## What it is / when to use it

`ToolNode` is PenguiFlow’s integration surface for exposing external tools to `ReactPlanner` with minimal wrapper code.

Use ToolNode when you want to:

- connect to MCP servers (via FastMCP),
- connect to HTTP APIs (via UTCP/OpenAPI discovery),
- expose tools with consistent schemas, retries, timeouts, concurrency, and artifact handling.

## Non-goals / boundaries

- ToolNode does not replace your service’s auth/policy layer; it helps you connect to tools safely.
- Presets are convenience configs for development; production should treat tool servers as owned services.
- ToolNode is not a “global singleton” by default; you are responsible for lifecycle (connect, reuse, close where relevant).

## Contract surface

### Core types

The relevant surfaces live in:

- `penguiflow.tools.node.ToolNode`
- `penguiflow.tools.config.ExternalToolConfig`
- `penguiflow.tools.presets.get_preset`

At a high level:

- `ToolNode.connect()` performs discovery (and may perform auth handshakes depending on configuration).
- `ToolNode.get_tools()` returns specs to include in the planner catalog.
- Tool names are namespaced: `{toolnode_name}.{tool_name}` (example: `github.create_issue`).

### Discovery + catalog build

Common patterns:

- a shared `ModelRegistry` for typed args/out models,
- one or more ToolNodes that perform discovery,
- a single combined catalog passed to `ReactPlanner`.

For planner-level filtering and tool discovery (`tool_search`, `tool_get`, deferred activation), see:

- **[Tool discovery & filtering](tool-discovery-and-filtering.md)**

## Operational defaults

- Call `await tool_node.connect()` at service startup (or warm it on first request) and reuse the same ToolNode across sessions.
- Use `tool_filter` (or equivalent) to expose only a safe subset of tools to the LLM.
- Set `ExternalToolConfig.max_concurrency` conservatively for external APIs (3–5 is a typical starting point).
- Prefer bounded outputs:
  - small tool outputs are returned inline,
  - large/binary content should become artifacts (see Tools docs).

## Failure modes & recovery

### Tool discovery fails

**Likely causes**

- missing local dependencies (Node.js for `npx`-based presets)
- missing env vars (config uses `${VAR}` substitution and can be fail-fast)
- network/auth failures to the tool server

**Fix**

- validate config with the same environment your worker uses
- use explicit timeouts/retries (and log failures) so “connect” doesn’t hang silently
- run MCP servers as services in production and connect via URL instead of spawning via `npx`

### Planner sees tools you didn’t intend

**Fix**

- apply ToolNode-level filtering and planner-level tool visibility/policy (belt + suspenders)
- avoid mixing tenant-specific tools into a shared global ToolNode without visibility controls

## Observability

Recommended:

- emit a config snapshot at startup (max concurrency, retries, timeouts, tool filter)
- record connect/discovery duration and tool counts (per ToolNode)
- record tool call latency/error rates via planner `event_callback`

See **[Planner observability](observability.md)** and **[Tools configuration](../tools/configuration.md)**.

## Security / multi-tenancy notes

- Don’t leak auth tokens into `llm_context`.
- Assume an LLM can try to call any visible tool: enforce allowlists and require HITL for write/external side effects.
- Keep per-tenant tool visibility separate from shared process state (use `ToolVisibilityPolicy` on planner calls when needed).

## Runnable example: connect an MCP preset

This example prints discovered tool names.

```python
from penguiflow import ModelRegistry
from penguiflow.tools.node import ToolNode
from penguiflow.tools.presets import get_preset

registry = ModelRegistry()

github = ToolNode(config=get_preset("github"), registry=registry)
await github.connect()

for spec in github.get_tools():
    print(spec.name)
```

!!! note
    Presets are for local development and often use `npx -y ...` (Node.js required).
    For production, prefer running MCP servers as separate services and connecting via URL.

## Configuration highlights (what matters operationally)

`ExternalToolConfig` supports:

- env var substitution via `${VAR}` in config fields,
- tool filtering (`tool_filter`) to expose a safe subset,
- per-tool source concurrency (`max_concurrency`) to protect rate-limited backends,
- retries and timeouts (`retry_policy`, `timeout_s`),
- artifact extraction for large/binary content.

See the curated runbooks:

- **[Tools configuration](../tools/configuration.md)**
- **[OAuth & HITL](../tools/oauth-hitl.md)**
- **[MCP resources](../tools/mcp-resources.md)**
- **[Artifacts & resources](../tools/artifacts-and-resources.md)**
- **[State store](../tools/statestore.md)**

## Troubleshooting checklist

- **`connect()` hangs**: set explicit timeouts; verify network/auth; ensure spawned dependencies exist (Node.js for presets).
- **Tools missing**: confirm discovery succeeded and `tool_filter` isn’t excluding them.
- **Too many requests / rate-limits**: lower `max_concurrency`, add retries/backoff, and reduce planner parallelism hints.
