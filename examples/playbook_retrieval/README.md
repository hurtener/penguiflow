# Playbook Retrieval

Shows how to launch a subflow ("playbook") from a controller node using `call_playbook`.
The playbook runs a three-step pipeline — retrieve, rerank, compress — and returns the
final summary to the parent controller.

## What happens

1. The controller wraps the incoming message payload into a small dict (`{"query": ...}`)
   and calls `call_playbook(build_retrieval_playbook, message)`.
2. `build_retrieval_playbook` constructs a standalone `PenguiFlow` with nodes for
   retrieval, reranking, and compression.
3. `call_playbook` propagates the parent message's headers/trace ID, waits for the first
   result emitted to the playbook's Rookery, and stops the subflow.
4. The controller replaces its payload with the returned summary and emits it downstream.

## Run it

```bash
uv run python examples/playbook_retrieval/flow.py
```

Example output:

```
antarctic krill-doc-1 :: compressed
```

Feel free to adjust the playbook (add rerank scoring, introduce retries) — the helper
cleans up after itself even if the controller task is cancelled.
