# Playbook Retrieval

This example demonstrates how a controller node can spin up a subflow playbook
to execute a retrieval → rerank → compression pipeline. The parent flow keeps
the same `trace_id` and headers, while the playbook returns the first payload
emitted to the Rookery queue.

Run it with:

```bash
uv run python examples/playbook_retrieval/flow.py
```
