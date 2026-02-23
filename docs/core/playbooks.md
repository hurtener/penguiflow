# Playbooks (subflows)

## What it is / when to use it

A **playbook** is a subflow you execute as a unit, typically to:

- reuse a complex pipeline (retrieve → rank → summarize),
- isolate a “mini graph” behind a single call boundary,
- enforce consistent timeouts/retries and observability around a reusable workflow.

PenguiFlow exposes `call_playbook(...)` to execute a playbook and return its first egress result.

## Non-goals / boundaries

- Playbooks are not a module system; they are a runtime composition tool.
- A playbook is a separate `PenguiFlow` instance. It does not automatically share queues, middleware, or state with the parent runtime.
- `call_playbook` is a best-effort integration point; if you need a fully distributed “subflow execution service”, use remote transports + a `StateStore`.

## Contract surface

### Playbook factory

A playbook is a callable that returns:

- a `PenguiFlow` instance, and
- an optional `ModelRegistry` used for runtime validation.

### `call_playbook(...)`

`call_playbook(playbook, parent_msg, timeout=None, runtime=None)`:

- runs the playbook flow,
- emits `parent_msg` as the ingress message,
- fetches the first Rookery result,
- returns:
  - `result.payload` if the result is a `Message`, otherwise
  - the raw result.

Cancellation propagation:

- if you pass `runtime=<parent PenguiFlow>` and the message has a `trace_id`,
  - `call_playbook` mirrors trace cancellation from the parent runtime into the subflow.

## Operational defaults (recommended)

- Use envelope messages (`Message`) when calling playbooks so correlation, deadlines, and cancellation behave deterministically.
- Keep playbooks small and purpose-built; avoid “god playbooks” that become hard to test and evolve.
- If a playbook touches external services, define explicit node-level timeouts and retry policy in the playbook itself.

## Failure modes & recovery

- **Subflow never returns**: the playbook has no egress node (nothing routes to Rookery).
  - Fix: ensure the playbook graph has at least one node with no outgoing edges that returns/emits a value.
- **Cancellation doesn’t propagate**: you did not pass `runtime=` or you aren’t using trace-scoped envelopes.
  - Fix: pass the parent runtime into `call_playbook` and ensure messages have `trace_id`.
- **Unexpected envelope loss**: playbook nodes return payloads instead of `Message` when you intended to preserve headers/meta.
  - Fix: adopt an envelope-consistent playbook style (Message-in → Message-out) or validate using `assert_preserves_message_envelope`.

## Observability

`call_playbook` runs a separate flow instance; attach observability to the playbook flow:

- middleware (runtime `FlowEvent` capture),
- `StateStore` event persistence (audit).

Operational pattern:

- reuse the same middleware factory you use for parent flows (structured logging + derived metrics),
- include `trace_id` in logs for correlation.

## Security / multi-tenancy notes

- Treat playbooks like any other flow: enforce tenant boundaries at ingress (`Headers.tenant`) and do not mix tenants inside a trace.
- If playbooks use external tool integrations, ensure secrets are injected via env/secret manager and never logged.

## Runnable examples

The repo contains a playbook example you can run:

```bash
uv run python examples/playbook_retrieval/flow.py
uv run python examples/routing_with_playbooks/flow.py
uv run python examples/roadmap_status_updates_subflows/flow.py
```

### Minimal example: calling a playbook from a parent node

This example demonstrates the *shape* of playbook invocation and cancellation mirroring.

```python
from __future__ import annotations

import asyncio

from penguiflow import Headers, Message, Node, NodePolicy, call_playbook, create


def make_subflow():
    async def sub(msg: Message, _ctx) -> Message:
        return msg.model_copy(update={"payload": {"subflow": True, "payload": msg.payload}})

    sub_node = Node(sub, name="sub", policy=NodePolicy(validate="none"))
    sub_flow = create(sub_node.to())
    return sub_flow, None


class Parent:
    def __init__(self) -> None:
        self.runtime = None

    async def run_subflow(self, msg: Message, _ctx) -> Message:
        assert self.runtime is not None
        result = await call_playbook(make_subflow, msg, timeout=2.0, runtime=self.runtime)
        return msg.model_copy(update={"payload": {"parent": True, "sub_result": result}})


async def main() -> None:
    parent = Parent()
    parent_node = Node(parent.run_subflow, name="parent", policy=NodePolicy(validate="none"))

    flow = create(parent_node.to())
    parent.runtime = flow
    flow.run()

    message = Message(payload={"hello": "world"}, headers=Headers(tenant="demo"))
    await flow.emit(message, trace_id=message.trace_id)
    print(await flow.fetch(trace_id=message.trace_id))
    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- If you need retries/timeouts, configure them on nodes inside the playbook (not only in the parent).
- If you need distributed pause/resume and audit history, integrate a `StateStore` and consider remote transports instead of nested subflows.

