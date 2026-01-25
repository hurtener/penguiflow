# A2A gRPC Server

This example spins up an A2A gRPC server backed by a simple echo flow, then
invokes the `SendMessage` RPC to verify end-to-end wiring.

## How to run

Install the gRPC extras (once):

```bash
uv sync --extra a2a-grpc
```

Then run the example:

```bash
uv run python examples/a2a_grpc_server/flow.py
```

or, if you already have a local venv:

```bash
pip install -e ".[a2a-grpc]"
python examples/a2a_grpc_server/flow.py
```

## What to expect

The script starts an ephemeral gRPC server, sends a user message, and prints the
task id plus the final task state, e.g.

```
Task <id> state: TASK_STATE_COMPLETED
```
