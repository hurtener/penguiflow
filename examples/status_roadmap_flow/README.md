# Roadmap status orchestration flow

This example demonstrates how to orchestrate a multi-branch PenguiFlow pipeline
that surfaces roadmap progress to a websocket UI. It shows how to:

- Emit lifecycle updates (`status`, `roadmap_step_*`) before, during, and after
  each node in a subflow.
- Route a single user query to specialised subflows (code analysis vs data
  summary) and return a structured `FlowResponse`.
- Perform parallel work inside a node with `map_concurrent` while still sending
  per-step progress messages.
- Stream final response chunks from the synthesis node after all subflows
  complete.

The folder contains a runnable flow, a Mermaid diagram, and a pytest exercising
all the moving pieces so downstream teams can copy the scaffold and plug in
their own subflows.

## Flow overview

1. `announce_start` emits the initial websocket update (`"Determining message
   path"`).
2. `triage` inspects the user text and populates metadata shared across the
   flow.
3. `dispatcher` routes the trace to either the code-analysis or data-summary
   branch.
4. Each branch publishes its roadmap (`roadmap_step_list`) and emits
   `roadmap_step_status` updates for every step. The code branch runs
   `code_inspect` with `map_concurrent` to demonstrate parallel fan-out with
   live progress updates.
5. Branch terminals materialise a `FlowResponse` and forward it to
   `synthesize`, which streams two final chunks and marks the concluding step
   as done (`roadmap_step_status="ok"`).

The rendered graph (`flow.mmd`) visualises the wiring:

```text
$ uv run python - <<'PY'
from examples.status_roadmap_flow.flow import mermaid_diagram
print(mermaid_diagram())
PY
```

`flow.mmd` contains the output of the command above so you can embed it in your
own docs or paste it into the [Mermaid Live Editor](https://mermaid.live/).

## Running the example

```bash
uv run python examples/status_roadmap_flow/flow.py
```

The script prints websocket-friendly status payloads, the streamed synthesis
chunks, and the final message emitted to Rookery. You can swap in your own
subflow logic while keeping the helper utilities (`emit_status`,
`FlowResponse`) unchanged.

## Websocket payload contract

All intermediary updates conform to the following shape (see
`StatusUpdate` in `flow.py`):

```json
{
  "status": "thinking",
  "roadmap_step_id": 2,
  "roadmap_step_status": "running",
  "message": "Inspecting payments.py"
}
```

The roadmap broadcast contains a list of steps:

```json
{
  "status": "thinking",
  "roadmap_step_list": [
    {"id": 1, "name": "Parse files", "description": "Load and tokenize the candidate modules"},
    {"id": 2, "name": "Inspect modules", "description": "Review each module in parallel to collect findings"},
    {"id": 3, "name": "Draft code report", "description": "Summarize findings and prepare a structured FlowResponse"},
    {"id": 4, "name": "Synthesize final reply", "description": "Combine subflow output and compose the user response"}
  ]
}
```

The terminal node always finishes by emitting a final status update for step 4
with `roadmap_step_status="ok"` and streams chunks via `Context.emit_chunk`
until the last `done=True` chunk is sent.

## Testing

```bash
uv run pytest tests/examples/test_status_roadmap_flow.py
```

The test asserts that:

- The correct branch is selected and the roadmap is broadcast.
- Every roadmap step sends a trailing `"ok"` update.
- Parallel step updates (`Inspecting app.py` / `Inspecting payments.py`) are
  observed even though they run concurrently.
- The synthesis node streams two chunks followed by the final message whose
  metadata includes the serialized `FlowResponse`.

## Files

- `flow.py` – Runnable flow, helper models, and `build_flow()` factory.
- `flow.mmd` – Pre-rendered Mermaid diagram for the full graph.
- `README.md` – This document.
- `tests/examples/test_status_roadmap_flow.py` – Integration test covering the
  roadmap contract.