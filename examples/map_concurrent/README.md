# map_concurrent Pipeline

Highlights the `map_concurrent` helper by scoring a batch of document ids inside a node.
The helper fans the work out while respecting a semaphore, so you can process batches
without overwhelming upstream APIs.

## Flow stages

1. `seed` node adds a list of document ids to the message payload.
2. `score` node calls `map_concurrent(docs, worker, max_concurrency=2)` to score each doc.
3. `summary` node picks the best score and emits a human-readable string.

## Run it

```bash
uv run python examples/map_concurrent/flow.py
```

Expected output resembles:

```
top doc: doc-5 score=0.40
```

Try adjusting `max_concurrency` to observe the effect on throughput, or swap the worker
body for real I/O such as HTTP requests.
