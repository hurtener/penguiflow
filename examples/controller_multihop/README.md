# Controller Multi-hop Loop

Demonstrates how a controller node can think over multiple hops using the `WM`/`FinalAnswer`
models shipped with PenguiFlow.

## Flow anatomy

- The controller node is created with `allow_cycle=True` and wired to itself via
  `controller.to(controller)`.
- Each iteration receives the previous `WM` payload, appends a new fact, and re-emits the
  message to itself.
- When the hop count reaches a threshold, the controller produces a `FinalAnswer`, which
  PenguiFlow automatically forwards to Rookery.

## Guardrails

The runtime increments `WM.hops`, tracks `WM.tokens_used`, and checks the configured
`budget_hops`, `budget_tokens`, and `Message.deadline_s`. If any limit is exceeded,
PenguiFlow returns a `FinalAnswer` with an exhaustion message instead of looping forever.

## Run it

```bash
uv run python examples/controller_multihop/flow.py
```

On completion you should see something like:

```
Token budget exhausted
```

Try changing `budget_hops`, `budget_tokens`, or adding simulated latency in the controller
to see how the runtime enforces deadlines and budgets.
