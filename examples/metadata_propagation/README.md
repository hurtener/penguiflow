# Metadata propagation demo

This example shows how PenguiFlow's `Message.meta` dictionary lets nodes attach
and consume auxiliary context (such as retrieval cost or model choices) without
altering the main payload.

## Running

```bash
uv run python examples/metadata_propagation/flow.py
```

## What it does

1. `annotate_cost` records a `retrieval_cost_ms` entry on the incoming message.
2. `summarize` reads that metadata, adds summarizer details, and updates the
   message payload with a formatted summary string.
3. The final node forwards the enriched message to Rookery; the script prints
   the payload and metadata so you can verify the round-trip.

Metadata travels automatically through the runtime, subflows, and streaming
helpers, so you can enrich messages with debugging, attribution, or billing
signals while keeping payloads clean.
