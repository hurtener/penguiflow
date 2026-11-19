# A2A gRPC Server

This example serves a `ReactPlanner` agent through `A2AService` and the A2A gRPC
binding, then calls the `SendMessage` RPC to check the wiring end to end.

The planner uses a scripted, offline LLM client that calls an `echo` tool once and
then answers, so the example runs without API keys.

## How to run

Install the gRPC extras (once):

```bash
uv sync --extra a2a-grpc
```

Then run the example:

```bash
uv run python examples/a2a_grpc_server/main.py
```

## What to expect

The script starts an ephemeral gRPC server, sends a user message, and prints the
task id, the final task state and the planner's answer, e.g.

```
Task <id> state: TASK_STATE_COMPLETED
Answer: echo:hello grpc
```

To serve your own agent, replace `build_planner()` with your `ReactPlanner`
(real LLM client, your tools) and keep the rest.
