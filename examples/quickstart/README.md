# Quickstart Flow

This example is the smallest end-to-end PenguiFlow pipeline. It wires three typed
nodes (`triage → retrieve → pack`) and demonstrates:

- registering Pydantic models with `ModelRegistry`
- validating inputs/outputs via `NodePolicy(validate="both")`
- emitting a message into OpenSea and fetching the result from Rookery

## How to run

```bash
uv run python examples/quickstart/flow.py
```

or, if you already have the local venv active:

```bash
python examples/quickstart/flow.py
```

## What to expect

The script prints a `PackOut.prompt` string that reflects the triaged topic and the
number of documents retrieved, e.g.

```
[metrics] summarize 2 docs
```

This verifies that the message traveled through all three nodes with validation enabled
at each hop.
