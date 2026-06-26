# Phase 007: Playground backend — serve UI components by id + message placement

## Objective
Make the playground backend serve store-backed UI components end-to-end: verify the existing `artifact_stored` SSE
event and artifact GET endpoints pass the `penguiflow_ui_component` namespace through and serve the stored JSON by
id; and, per decision 10 (option a), include `message_id`/`default_message_id` on `penguiflow_ui_component`
`artifact_stored` frames so an `artifact`-only UI component arrives with a render slot. No default flip — `inline`
stays exactly as today.

## Tasks
1. Verify the `artifact_stored` SSE event and artifact GET endpoints surface the `penguiflow_ui_component` namespace
   and serve the stored JSON by id (no happy-path change expected beyond surfacing the flag).
2. Include `message_id`/`default_message_id` on `penguiflow_ui_component` `artifact_stored` frames in the live
   `/chat/stream` path (mirror the stream-chunk path at `playground.py:1622-1628`).
3. Confirm NO default flip: `inline` writes no UI components to the store and fires no `artifact_stored` for UI
   components.

## Detailed Steps

### Step 1: Verify namespace + GET-by-id pass-through
- Inspect `penguiflow/cli/playground.py`: confirm the `artifact_stored` SSE event (around `playground.py:430-443`)
  forwards `source.namespace` and that the artifact GET endpoint can serve `application/json` UI-component bytes by
  id. The rails already exist; this is verification + any minimal wiring, not a rewrite.

### Step 2: Message placement (decision 10, option a — backend)
- The live `/chat/stream` backend injects `default_message_id` into STREAM CHUNKS (`playground.py:386-414`) but NOT
  into `artifact_stored` frames (`playground.py:430-443`). For frames whose `source.namespace ==
  "penguiflow_ui_component"`, inject `message_id`/`default_message_id` the same way the stream-chunk path does
  (mirror `playground.py:1622-1628`). The backend already knows the active message id.
- Do NOT use the frontend-side option (attaching to the active `agentMsgId`).

### Step 3: Confirm no default flip
- Confirm `load_agent()` still defaults to `InMemoryStateStore()` (`playground.py:773-775`) and
  `_discover_artifact_store()` still falls back to `PlaygroundArtifactStore` (`playground.py:1023-1044`) — these are
  unchanged. The narrow claim: in `inline` mode the planner's rich-output UI writes stay off (no store write, no
  `artifact_stored` for UI components). The `artifact_chunk` SSE path (`playground.py:416`) stays for
  `inline`/`both`.

## Required Code

```python
# Target file: penguiflow/cli/playground.py
# In the /chat/stream SSE handler, when forwarding an artifact_stored frame for the UI-component namespace,
# attach the active message id the same way stream chunks do (mirror playground.py:1622-1628):
#
#   if frame.get("event_type") == "artifact_stored":
#       source = frame.get("extra", {}).get("source", {})
#       if source.get("namespace") == "penguiflow_ui_component":
#           frame.setdefault("message_id", default_message_id)
#           frame.setdefault("default_message_id", default_message_id)
#
# Match the exact frame/field shape already used for stream chunks at playground.py:386-414 / :1622-1628.
```

## Exit Criteria (Success)
- [ ] An `artifact`-mode UI component produces an `artifact_stored` SSE frame carrying `source.namespace ==
      "penguiflow_ui_component"` and a `message_id`/`default_message_id` (so the frontend has a render slot).
- [ ] The artifact GET endpoint serves the stored UI-component JSON by id (the opaque `artifact_id`).
- [ ] `inline` mode: the playground fires NO `artifact_stored` for UI components and writes none to the store; the
      `artifact_chunk` SSE path still serves `inline`/`both`.
- [ ] No default-store flip: `load_agent()` still defaults to `InMemoryStateStore()`;
      `_discover_artifact_store()` fallback unchanged.
- [ ] Backwards-compat: the existing test suite passes unchanged on the default (`inline`) path.

## Implementation Notes
- Depends on Phase 003 (the `artifact_stored` event for `render_*` is what the backend forwards; in `both` mode the
  inline chunk carries the shared `artifact_id`).
- Wording precision: the playground is NOT globally "artifacts-off" — endpoints and binary-artifact storage are live
  today. The accurate claim is narrow: in `inline` mode the planner's rich-output UI writes stay off.
- This is mostly verification + the message-id injection; no happy-path change beyond surfacing the new flag and the
  decision-10 message placement.

## Verification Commands
```bash
# Playground-related tests (backend SSE + endpoints)
uv run pytest tests/ -k "playground or artifact_stored or artifact" -q

uv run ruff check . && uv run mypy
```
