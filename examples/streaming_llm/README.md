# Streaming LLM Demo

`stream_answer.py` shows how `ReactPlanner` picks up streaming chunks from a tool and passes them on through `PlannerEvent` callbacks while the tool is still running.

## Running

```bash
uv run python examples/streaming_llm/stream_answer.py
```

Example output:

```
Streaming answer: PenguiFlow is a lightweight async agent orchestrator with typed nodes, reliable retries, and a JSON-only ReAct planner.
Final payload: {"answer": "PenguiFlow is a lightweight agent orchestrator."}
```

Each streamed token fires a `stream_chunk` event and prints right away. The chunks are also kept on the resulting trajectory, in `result.metadata["steps"][0]["streams"]`.
