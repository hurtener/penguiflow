# Roadmap Status Updates (Subflows)

This example is a superset of `examples/roadmap_status_updates/` and focuses on
launching typed subflows from roadmap steps. It keeps the same UI contracts for
status updates and streamed chunks while demonstrating two advanced patterns:

* A metadata enrichment subflow that uses `map_concurrent` to analyse document
  sources with bounded concurrency.
* A diagnostics subflow that fans out work across branch-specific nodes and
  rejoins the results with `join_k`.

Both subflows are invoked via `Context.call_playbook`, so they inherit the
parent trace ID, headers, and cancellation semantics automatically.

## Running the example

```bash
uv run python examples/roadmap_status_updates_subflows/flow.py
```

During execution the flow populates two in-memory telemetry buffers:

* `STATUS_BUFFER` – ordered `StatusUpdate` events suitable for websocket
  payloads.
* `CHUNK_BUFFER` – streamed `StreamChunk` instances emitted during the final
  synthesis step.

Use these buffers in tests or adapt them to your own transport (e.g. send the
entries to a websocket client).

## Branch behaviour

1. **Triage:** Routes to the documents or bug branch and notifies the UI which
   roadmap was selected.
2. **Documents branch:**
   * `parse_documents` seeds the source list.
   * `extract_metadata` spawns a subflow that runs a `map_concurrent`
     worker. The subflow returns an updated `DocumentState` populated with
     deterministic metadata digests.
   * Subsequent steps generate a summary and assemble the artifact bundle.
3. **Bug branch:**
   * `run_diagnostics` launches a diagnostics playbook. The playbook fans out to
     two workers, each producing a `DiagnosticTask`. A `join_k("join_diagnostics", 2)`
     node aggregates the results before merging them back into the parent
     `BugState`.
   * `propose_fix` packages the results into a `FlowResponse` for synthesis.
4. **Final synthesis:** Streams two chunks while composing the final
   `FinalAnswer` and emits the closing roadmap status.

The helper `export_mermaid` function still writes a Mermaid diagram to
`flow.mermaid.md`:

```bash
python - <<'PY'
from examples.roadmap_status_updates_subflows.flow import build_flow, export_mermaid
flow, _ = build_flow()
export_mermaid(flow)
PY
```

## Tests

A dedicated test module exercises both branches and asserts that the subflows
return the enriched states:

```bash
uv run pytest tests/examples/test_status_roadmap_subflows.py
```

The tests verify the emitted status ordering, streamed chunks, and the
structured artifacts included in the final `FlowResponse`.
