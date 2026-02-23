# Routers & routing policies

## What it is / when to use it

Routing is how you build **branching graphs** in the core runtime:

- “if this, send to A; else send to B”
- “validate a discriminated union and route by kind”
- “apply a config-driven policy to allow/deny routes at runtime”

Use routing when your workflow is a DAG with decision points and you want the **graph** (not the planner) to determine control flow.

## Non-goals / boundaries

- Routers are not planners: they do not invent steps or choose tools based on LLM reasoning.
- Routing policies are not an authz engine; they are a hook to select targets. You must implement security separately.
- Routers do not persist decisions by default. If you need auditability, persist runtime events via a `StateStore`.

## Contract surface

### `predicate_router(...)`

`predicate_router(name, predicate, policy=...)` returns a `Node` that:

- calls `predicate(message)` to produce targets, then
- emits the **same** message to those targets.

The predicate can return:

- a `Node`, successor node name (`str`), or a sequence of them, or
- `None` to drop the message (no routing).

If a returned target name does not match a successor, the router raises `KeyError`.

### `union_router(...)`

`union_router(name, union_model, policy=...)` routes by validating the input against a Pydantic discriminated union:

- it tries to read `kind` from the validated model, otherwise uses the class name
- it routes to a successor whose node name matches that string

If no successor matches, it raises `KeyError`.

### Routing policies

Both router helpers accept an optional `policy` that can:

- return a routing decision (same types as the predicate), or
- return `None` to drop the message.

Policies can be sync or async. The runtime passes a `RoutingRequest` with:

- `message`, `context`, `node`, `proposed` targets, and `trace_id`.

Built-in helper:

- `DictRoutingPolicy(mapping, default=..., key_getter=...)`
  - supports loading from JSON/env via `from_json(...)` and `from_env(...)`

## Operational defaults (recommended)

- Keep routing decisions **pure** (no side effects). Emit side effects in dedicated nodes.
- Keep routing keys low-cardinality and stable (e.g. `kind`, `tenant`, feature flag).
- Prefer one routing layer per “decision point”; complex nested routing becomes hard to reason about under retries.

## Failure modes & recovery

- **`KeyError: No successor named ...`**: you returned a target name that is not connected as an outgoing successor.
  - Fix: connect the router to the target node in `create(...)` and ensure `Node(name=...)` matches.
- **Message disappears**: your predicate or policy returned `None`.
  - Fix: add explicit logging (or attach middleware) and ensure the “drop” path is intentional.
- **Unexpected types routed**: `union_router` validated your input into an unexpected branch.
  - Fix: make your union discriminator explicit (e.g. `kind: Literal[...]`) and validate at boundaries.

## Observability

Routing decisions are visible via standard runtime `FlowEvent` node lifecycle events for the router node.

Recommended:

- include `trace_id` and tenant headers in envelope flows,
- attach `log_flow_events(...)` middleware and derive metrics from router node latency and error rates.

See **[Telemetry patterns](../observability/telemetry-patterns.md)**.

## Security / multi-tenancy notes

- Do not treat routing policies as authorization. They are easy to bypass if an attacker can craft messages.
- If routing decisions use `Headers.tenant`, ensure tenant is set and enforced at ingress.

## Runnable examples

The repo contains runnable routing examples:

```bash
uv run python examples/routing_predicate/flow.py
uv run python examples/routing_union/flow.py
uv run python examples/routing_policy/flow.py
```

### Minimal example: predicate router

```python
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from penguiflow import Node, create, predicate_router


class In(BaseModel):
    route: str
    value: int


async def handle_a(msg: In, _ctx) -> dict:
    return {"handled_by": "a", "value": msg.value}


async def handle_b(msg: In, _ctx) -> dict:
    return {"handled_by": "b", "value": msg.value}


router = predicate_router("route", lambda msg: "a" if msg.route == "a" else "b")
a = Node(handle_a, name="a")
b = Node(handle_b, name="b")


async def main() -> None:
    flow = create(
        router.to(a, b),
        a.to(),
        b.to(),
    )
    flow.run()

    await flow.emit(In(route="a", value=1))
    print(await flow.fetch())
    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- If routing is “random”, confirm you’re not using global mutable state in the predicate/policy.
- If you need “route filtering”, add a policy that constrains the proposed set rather than returning arbitrary nodes.
- If you’re building an LLM agent, consider using `ReactPlanner` instead of complex routing graphs (planner docs: **[Overview](../planner/overview.md)**).

