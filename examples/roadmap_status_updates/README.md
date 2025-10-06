# Roadmap Status Updates Example

This example demonstrates how to build a multi-branch PenguiFlow agent that:

* triages incoming user queries into dedicated subflows,
* publishes roadmap plans and per-step status updates over a websocket channel,
* fans out work in parallel within a subflow, and
* synthesizes a final response from intermediate `FlowResponse` payloads.

It mirrors the product requirements for downstream teams that need rich UI
telemetry while executing heterogeneous agent pipelines.

## Running the example

```bash
uv run python examples/roadmap_status_updates/flow.py
```

The script accepts a single query string (edit the call in `main()` if needed)
and prints the final answer. During execution, the example populates two
in-memory telemetry buffers:

* `STATUS_BUFFER` – ordered `StatusUpdate` events suitable for the websocket
  payloads described below.
* `CHUNK_BUFFER` – streamed `StreamChunk` instances emitted while the final
  synthesis step is running.

Inspect these buffers in your own integration to replay the updates or forward
them to a frontend.

## Status message contract

Every node that represents a roadmap step emits a `StatusUpdate` with the
fields expected by the UI:

```json
{
  "status": "thinking",
  "message": "Assembling HTML report",
  "roadmap_step_id": 4,
  "roadmap_step_status": "running"
}
```

Key behaviours:

1. **Before routing** we send `{"message": "Determining message path"}` to
   announce that the agent is triaging the request.
2. **Plan broadcast:** the first node inside each branch publishes the entire
   roadmap via `roadmap_step_list` so the UI can render progress bars.
3. **Per-step lifecycle:** for each `roadmap_step_id` we emit a `running`
   update followed by a terminal `ok` message (`"message": "Done!"`). No other
   updates are sent after the terminal `ok` event.
4. **Final synthesis:** the last step in the roadmap (`id=99` in this example)
   streams text chunks and finally emits its `Done!` status when the final
   answer is ready.

## Flow layout

The flow contains three logical sections:

1. **Triage:** routes to either the document summarizer or the bug triage
   subflow and notifies the UI about the chosen branch.
2. **Subflows:**
   * `documents_*` parses repository files, extracts metadata in parallel using
     `map_concurrent`, and generates a report packaged inside a
     `FlowResponse`.
   * `bug_*` collects incident context and produces a remediation plan wrapped
     in the same `FlowResponse` contract.
3. **Final synthesis:** combines the query, branch metadata, and the subflow’s
   `FlowResponse` to produce the final `FinalAnswer`. This node also emits
   streaming chunks to demonstrate how to surface incremental output to the UI
   before the `Done!` status.

The helper `export_mermaid` function writes a Mermaid diagram to
`flow.mermaid.md`:

```bash
python - <<'PY'
from examples.roadmap_status_updates.flow import build_flow, export_mermaid
flow, _ = build_flow()
export_mermaid(flow)
PY
```

## FlowResponse contract

Each subflow terminates with a `FlowResponse` that matches the downstream
schema:

```python
FlowResponse(
    raw_output="Summarized 3 files with 3 metadata entries.",
    artifacts={"sources": [...], "metadata": [...]},
    session_info="documents-branch",
)
```

The final synthesis node consumes that object, augments the artifacts with the
selected route, and produces the user-facing `FinalAnswer`. Downstream teams can
use this scaffold to bolt in their own LLM calls or external tools while keeping
all UI updates consistent.

## Tests

A dedicated test module exercises both branches, asserts the status ordering,
verifies the streamed chunks, and ensures the final `FinalAnswer` includes the
subflow output:

```bash
uv run pytest tests/test_examples_roadmap.py
```

Use the tests as a template when cloning this scaffold into your own agent.