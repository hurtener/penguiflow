# Roadmap & Websocket Status Patterns

This guide expands on the roadmap-enabled streaming pattern introduced in
`examples/roadmap_status_updates/`. It explains how to structure PenguiFlow
pipelines that drive rich front-end status indicators while preserving the
`FlowResponse` contract for downstream processing.

## Lifecycle overview

1. **Start signal** – before triage, emit a status message such as
   `{"status": "thinking", "message": "Determining message path"}`. This
   lets the UI display an activity indicator immediately.
2. **Route selection** – when a branch is selected, emit a short message that
   announces the chosen subflow.
3. **Plan broadcast** – the entry node of the chosen branch publishes the full
   roadmap via `roadmap_step_list`. Each element is a `RoadmapStep` with an `id`,
   `name`, and `description` so the UI can build a progress tracker.
4. **Per-step execution** – every roadmap step sends a pair of updates:
   `roadmap_step_status="running"` when work starts and `roadmap_step_status="ok"`
   with `"message": "Done!"` once the step finishes. No further updates are sent
   after the terminal `ok` message.
5. **Final synthesis** – the last step streams partial output via
   `Context.emit_chunk` while preparing the final answer. It finishes with a
   final `Done!` update for the same `roadmap_step_id`.

## Implementation tips

* Share a common helper (see `emit_status` in the example) to ensure every node
  emits consistent payloads.
* Connect each node that needs to emit status events to a dedicated
  `status_updates` sink. This keeps the main data path clean while letting you
  forward the updates to a websocket consumer.
* Use `map_concurrent` or `join_k` to demonstrate parallel work inside steps.
  The example runs metadata extraction concurrently to highlight the pattern.
* Return a `FlowResponse` from every subflow. The final synthesis node should
  merge the `raw_output` and `artifacts` into a user-facing `FinalAnswer` while
  preserving any diagnostic information in the response metadata.

## Testing strategy

The companion test module (`tests/test_examples_roadmap.py`) shows how to:

* reset in-memory telemetry buffers before each run,
* assert the ordering of `StatusUpdate` events,
* verify that the final chunk is marked with `done=True`, and
* inspect the final `FinalAnswer` for route- and branch-specific text.

Use the test harness as a template when replicating the pattern in your own
agents.