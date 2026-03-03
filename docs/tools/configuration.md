# Configuration (ToolNode)

## What it is / when to use it

This page documents how to configure external tools for `ReactPlanner` using `ToolNode`.

You need it when you are:

- connecting MCP servers or UTCP endpoints,
- tuning timeouts/retries/concurrency for production,
- enforcing safe tool visibility (read-only subsets, allowlists),
- wiring OAuth/HITL flows.

## Non-goals / boundaries

- This page does not describe every MCP/UTCP server; it documents PenguiFlow’s configuration contract.
- ToolNode does not replace your authorization/policy layer; it helps you safely connect to tool sources.
- “Presets” are for development convenience; production should treat tool servers as managed services.

## Contract surface

### Core types

The main config model is:

- `penguiflow.tools.config.ExternalToolConfig`

Used by:

- `penguiflow.tools.node.ToolNode`

Key enums:

- `TransportType`: `MCP | HTTP | UTCP | CLI` (UTCP/HTTP/CLI are supported via the `utcp` client)
- `AuthType`: `NONE | API_KEY | BEARER | COOKIE | OAUTH2_USER`

### Required fields

- `name`: namespace prefix for tools (example: `github`)
- `transport`: which protocol to use
- `connection`: MCP command or URL; UTCP manual/base URL

Tool names are namespaced: `{name}.{tool}` (example: `github.create_issue`).

### Environment variable substitution (`${VAR}`)

ToolNode substitutes `${VAR}` inside certain config fields at runtime.

Behavior:

- if the env var is present: it is substituted
- if it is missing: ToolNode raises `ToolAuthError` (fail-fast)

This is intentional: missing credentials should fail at startup/connect time, not later during agent execution.

!!! note
    Implementation detail: substitution uses a regex replace (`re.sub`) over `${...}` patterns, so multiple variables per string are supported.

### Auth modes (what they do)

- `AuthType.NONE`: no auth headers
- `AuthType.BEARER`: sends `Authorization: Bearer <token>` using `auth_config["token"]`
- `AuthType.API_KEY`: injects an API key header (`auth_config["api_key"]`, default header `X-API-Key`)
- `AuthType.COOKIE`: injects a cookie header (`cookie_name`, `cookie_value`)
- `AuthType.OAUTH2_USER`: user-scoped OAuth via HITL pause/resume (see **[OAuth & HITL](oauth-hitl.md)**)

ToolNode resolves auth:

- during `connect(...)` (connection-time headers), and
- per tool call (`ToolNode.call(...)`) so tokens can refresh/rotate.

### Resilience knobs

`ExternalToolConfig` includes:

- `timeout_s`: per-call timeout (default 30s)
- `retry_policy`: tenacity-based backoff (`max_attempts`, min/max wait, retryable HTTP status codes)
- `max_concurrency`: concurrency limit per ToolNode instance (default 10)

### Tool filtering (`tool_filter`)

`tool_filter` is an allowlist of regex patterns.

Semantics:

- ToolNode uses `re.match(pattern, tool_name)` (match from the start of the string).
- If `tool_filter` is `None`, all discovered tools are exposed.

Prefer allowlists for production.

### Artifact extraction and resources

ToolNode can extract large/binary content into artifacts and handle MCP resources:

- `ExternalToolConfig.artifact_extraction` controls the extraction pipeline
- MCP resources can generate tools like `{namespace}.resources_read` (see **[MCP resources](mcp-resources.md)**)

> **Note:** The extraction pipeline is an internal (plumbing) mechanism — it uses `ctx._artifacts` (the raw `ArtifactStore`) directly. Tool developers storing artifacts manually should use `ctx.artifacts` (the `ScopedArtifacts` facade) with `upload()`/`download()`/`list()`.

See **[Artifacts & resources](artifacts-and-resources.md)**.

## Operational defaults (recommended)

- **Start safe**:
  - use a narrow `tool_filter` (read-only patterns) before broadening
  - gate write/external tools behind HITL/policy
- **Bound concurrency**:
  - external SaaS: `max_concurrency=3..5`
  - internal services: start at `10` and tune
- **Make hangs impossible**:
  - set `timeout_s` explicitly for every ToolNode
  - keep retries bounded (default `max_attempts=3`)
- **Prefer service-based MCP**:
  - for production, run MCP servers as services and connect via URL
  - avoid `npx -y ...` in long-lived workers unless you own the environment

## Failure modes & recovery

### Missing `${VAR}` at startup

**Symptoms**

- ToolNode raises `ToolAuthError` while connecting or calling a tool

**Fix**

- ensure the environment is present in the worker process (not only your shell)
- prefer explicit env var names (avoid `${TOKEN}`)

### OAuth pauses but never completes

**Likely causes**

- no OAuth callback endpoint wired to store tokens
- missing `user_id` in `tool_context`
- distributed deployment without durable pause state

**Fix**

- implement OAuth callback + token persistence (see **[OAuth & HITL](oauth-hitl.md)**)
- configure a `StateStore` that supports planner pause persistence

### Rate-limits / 429 storms

**Fix**

- lower `max_concurrency`
- tune retry backoff and retryable status codes
- reduce planner parallelism (`planning_hints.max_parallel`)

## Observability

At minimum, record:

- connect/discovery duration and tool count per ToolNode
- planner tool call telemetry (`PlannerEvent(event_type="tool_call_*")`)
- error rates and retry counts by tool source
- artifact storage (`PlannerEvent(event_type="artifact_stored")`) when artifact extraction is enabled

See **[Planner observability](../planner/observability.md)**.

## Security / multi-tenancy notes

- Treat ToolNode config as sensitive (it may reference secrets via `${VAR}`).
- Never surface secrets into `llm_context`; keep secrets in `tool_context` or provider-specific stores.
- Enforce per-tenant tool visibility (and avoid mixing tenant-scoped ToolNodes without a visibility policy).

## Runnable example: safe, read-only GitHub ToolNode

```python
from __future__ import annotations

import asyncio

from penguiflow import ModelRegistry
from penguiflow.tools import AuthType, ExternalToolConfig, ToolNode, TransportType


async def main() -> None:
    registry = ModelRegistry()

    github = ToolNode(
        config=ExternalToolConfig(
            name="github",
            transport=TransportType.MCP,
            connection="npx -y @modelcontextprotocol/server-github",
            auth_type=AuthType.BEARER,
            auth_config={"token": "${GITHUB_TOKEN}"},
            tool_filter=["get_.*", "list_.*", "search_.*"],
            max_concurrency=3,
            timeout_s=30.0,
        ),
        registry=registry,
    )

    await github.connect()
    for spec in github.get_tools():
        print(spec.name)


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **Discovery fails**: verify dependencies (FastMCP/UTCP), env vars, and network reachability.
- **Too many tools**: add `tool_filter` allowlists and enforce planner tool visibility.
- **Calls are slow**: tune `timeout_s`, retries, and concurrency; check provider rate limits.
