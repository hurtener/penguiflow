# Fan-out and Join

Illustrates a classic fan-out/fan-in pattern using the `join_k` helper. A single message
is duplicated to two workers; once both responses arrive (sharing the same `trace_id`),
`join_k("join", k=2)` aggregates the payloads and forwards them to a summarizer node.

## Key ideas

- `join_k` buffers messages by `trace_id`, so each branch must forward the original
  message or a derivative that retains the `trace_id` field.
- As soon as `k` messages are collected, the helper emits either a batch payload or, when
  working with `Message` objects, a copy whose `payload` becomes a list of payloads.
- Downstream nodes can treat the result as a mini-batch for aggregation or consensus.

## Run it

```bash
uv run python examples/fanout_join/flow.py
```

Expected output:

```
task::A,task::B
```

Experiment by adding a third worker and updating `join_k("join", 3)` to observe how the
join waits for all branches before continuing.
