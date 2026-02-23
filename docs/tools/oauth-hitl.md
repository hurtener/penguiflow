# OAuth & HITL (ToolNode)

## What it is / when to use it

ToolNode supports **user-scoped OAuth** flows for external tools via:

- `AuthType.OAUTH2_USER` on `ExternalToolConfig`
- a pause/resume handoff using `ReactPlanner`’s HITL primitives

Use this when a tool must act on behalf of an end user (Slack, GitHub, Google Drive, etc.) and you can’t use a single static service token.

## Non-goals / boundaries

- PenguiFlow does not run your OAuth web app. You must provide callback handling and token persistence.
- OAuth refresh/rotation policies are provider-specific; the default `OAuthManager` only stores access tokens and an optional expiry.
- The OAuth handshake is a security-sensitive flow; this page describes the contract and operational runbook, not every security best practice.

## Contract surface

### ToolNode config

Enable user OAuth with:

- `ExternalToolConfig(auth_type=AuthType.OAUTH2_USER)`

### `tool_context` requirements

ToolNode requires:

- `ctx.tool_context["user_id"]` (required)
- `ctx.tool_context["trace_id"]` (optional but recommended for correlation)

### OAuth manager

ToolNode uses an auth manager (default implementation in `penguiflow.tools.auth`):

- `OAuthManager(providers=..., token_store=...)`

It must provide:

- `get_token(user_id, provider) -> token | None`
- `get_auth_request(provider, user_id, trace_id) -> payload`
- (outside ToolNode) a callback handler that stores the token so subsequent calls succeed

### Pause payload shape

When ToolNode needs OAuth, it pauses the planner with:

- `reason="external_event"`
- `payload` containing at least:
  - `pause_type: "oauth"`
  - `provider: <toolnode name>`
  - plus fields from `OAuthManager.get_auth_request(...)` (commonly: `auth_url`, `state`, `scopes`, `display_name`)

See **[Pause/resume (HITL)](../planner/pause-resume-hitl.md)** for the planner-level token persistence contract.

## Operational defaults

- Use a **durable StateStore** (planner pause state) if you run multiple workers or expect restarts.
- Use a **durable TokenStore** (OAuth tokens) so users don’t have to re-auth on every request.
- Treat `user_id` as an identity key and ensure it is tenant-scoped in multi-tenant systems.
- Use HTTPS for callback endpoints and validate OAuth `state` values.

!!! note
    `OAuthManager` expires pending OAuth `state` after ~10 minutes. If your UX requires longer, implement your own auth manager.

## Failure modes & recovery

### ToolNode raises `ToolAuthError` (“user_id required”)

**Fix**

- ensure your orchestrator sets `tool_context={"user_id": "...", ...}`

### Planner pauses, but resume fails (`KeyError`)

**Likely causes**

- pause record was only in-memory and the worker restarted
- StateStore does not implement `save_planner_state`/`load_planner_state`

**Fix**

- implement planner state persistence on your StateStore
- align pause token TTL with your OAuth callback latency

### OAuth completed but tool still pauses

**Likely causes**

- callback handler didn’t store the token (or stored it under the wrong key)
- token store is not shared across workers

**Fix**

- persist tokens in a shared store (DB/Redis/KMS-backed)
- ensure you use the same `(user_id, provider)` keying the auth manager expects

## Observability

Track at minimum:

- pause count by provider (`pause_type="oauth"`)
- time-to-resume distribution (p95/p99)
- OAuth callback success/failure rate (state invalid/expired, provider errors)
- token cache hit rate (how often `get_token` is non-null)

## Security / multi-tenancy notes

- Never put OAuth access tokens into `llm_context`.
- Treat `resume_token` and OAuth `state` as secrets.
- Store OAuth client secrets in a secret manager; do not commit them to config files.
- Tenant-scope `user_id` (e.g. `tenant:user`) or use a composite key in your TokenStore to avoid cross-tenant token reuse.

## Runnable example: a deterministic OAuth pause (test double)

This is a **no-network** example that demonstrates the pause payload shape using a minimal auth manager.

```python
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import PlannerPause, ReactPlanner, ToolContext


class Out(BaseModel):
    ok: bool


@tool(desc="A tool that requires OAuth", side_effects="external")
async def oauth_tool(_args: BaseModel, ctx: ToolContext) -> Out:  # type: ignore[valid-type]
    if not ctx.tool_context.get("oauth_ready"):
        await ctx.pause(
            "external_event",
            {
                "pause_type": "oauth",
                "provider": "demo",
                "display_name": "Demo OAuth",
                "auth_url": "https://example.invalid/oauth?state=demo",
                "state": "demo",
                "scopes": ["read"],
            },
        )
    return Out(ok=True)


async def main() -> None:
    registry = ModelRegistry()
    registry.register("oauth_tool", BaseModel, Out)  # permissive args for the demo
    catalog = build_catalog([Node(oauth_tool, name="oauth_tool")], registry)

    planner = ReactPlanner(llm="gpt-4o-mini", catalog=catalog)
    result = await planner.run("Call oauth_tool", tool_context={"session_id": "demo"})

    while isinstance(result, PlannerPause):
        # In real systems: open result.payload["auth_url"], handle callback, then resume.
        result = await planner.resume(
            result.resume_token,
            user_input="oauth_completed",
            tool_context={"session_id": "demo", "oauth_ready": True},
        )

    print(result.reason)


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **OAuth pauses in prod, resumes fail**: you need durable pause persistence (`StateStore.save_planner_state/load_planner_state`).
- **Users re-auth every time**: your TokenStore isn’t durable/shared, or `user_id` keying is inconsistent.
- **Tokens leak**: audit `llm_context` and logs; tokens must stay in tool-only surfaces.
