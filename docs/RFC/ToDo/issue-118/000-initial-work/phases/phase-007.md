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

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-06-26

### Summary of Changes

- **`penguiflow/cli/playground.py`** — `_event_frame()` `artifact_stored` branch (Step 2, the only
  behavioral change in this phase):
  - Hoisted `source = extra.get("source")` so it is reused both in the payload and the new gate.
  - After building the `artifact_stored` payload, added a guarded injection: when
    `isinstance(source, Mapping) and source.get("namespace") == "penguiflow_ui_component"` and the
    already-resolved `message_id` is not `None`, set `payload.setdefault("message_id", message_id)` and
    `payload.setdefault("default_message_id", message_id)`. This mirrors the stream-chunk path
    (`event.event_type == "stream_chunk"` block) which already computes `message_id` from
    `extra["message_id"]` (preferred) falling back to the `default_message_id` parameter, and the live
    `/chat/stream` consumer that passes `default_message_id=stream_message_id`.
  - `setdefault` is used so that an explicit `extra["message_id"]` (already folded into the local
    `message_id` variable) is never clobbered, and so a future caller that pre-populates the field wins.

- **`tests/test_playground_phase3.py`** — added three unit tests in `TestSSEEventFrames` plus two extra
  assertions on the existing `test_artifact_stored_event_frame`:
  - `test_artifact_stored_ui_component_carries_message_id` — namespace `penguiflow_ui_component` +
    `default_message_id="msg_active"` ⇒ frame carries both `message_id` and `default_message_id`
    (decision 10, option a; the render-slot exit criterion).
  - `test_artifact_stored_ui_component_without_active_message_id` — UI namespace but no message id
    available ⇒ no `message_id`/`default_message_id` injected (avoids a null/placeholder slot).
  - `test_artifact_stored_non_ui_namespace_no_message_id` — namespace `tableau` (binary/MCP) even with a
    `default_message_id` ⇒ no injection, existing shape preserved.
  - The existing `test_artifact_stored_event_frame` (string `source: "tableau"`) gained assertions that
    no `message_id`/`default_message_id` leak in — locking the `isinstance(source, Mapping)` guard.

### Key Considerations

- **Reused the resolved `message_id`, not the raw `default_message_id` parameter.** The Required-Code
  snippet in the phase referenced `default_message_id` directly, but the phase also says explicitly to
  "Match the exact frame/field shape already used for stream chunks at playground.py:386-414 / :1622-1628."
  The stream-chunk shape sets `payload["message_id"] = message_id`, where `message_id` is derived as
  `extra["message_id"]` (if a non-empty string) else the `default_message_id` arg. I followed the actual
  codebase shape: I gate on (and inject) the resolved `message_id`. In the live `/chat/stream` path
  `extra["message_id"]` is absent for UI `artifact_stored` events, so `message_id == stream_message_id`,
  i.e. the same value the snippet intended. This is strictly more correct (honors an explicit
  per-event message id) and consistent with every other branch in `_event_frame`.

- **Guarded with `isinstance(source, Mapping)` before `.get("namespace")`.** For binary/MCP artifacts
  `source` can be a plain string (the pre-existing `test_artifact_stored_event_frame` uses
  `source: "tableau"`). Calling `.get` on a string would raise `AttributeError`. The `isinstance` guard
  also matches the project's existing defensive style (the `stream_chunk` branch guards `meta` with
  `isinstance(meta, Mapping)`).

- **No injection when there is no message id.** If neither `extra["message_id"]` nor `default_message_id`
  yields a value, nothing is injected — we never emit `message_id: null`, which would give the frontend a
  bogus render slot. This is the conservative behavior and keeps the frame identical to today for any path
  that does not supply a message id.

- **Steps 1 and 3 are verification-only and required no code changes** (the phase frames them as
  verify + minimal wiring). I confirmed against the current code:
  - Step 1 (namespace + GET-by-id pass-through): the `artifact_stored` branch already forwards
    `source` (which carries `{"namespace": ...}`); `GET /artifacts/{artifact_id}` serves bytes by the
    opaque id and sets `media_type=ref.mime_type`. UI components are written by
    `penguiflow/rich_output/nodes.py` via `proxy.put_text(..., mime_type="application/json",
    namespace="penguiflow_ui_component", ...)`, so the GET endpoint serves the stored JSON with
    `Content-Type: application/json` with no change needed.
  - Step 3 (no default flip): `load_agent()` still defaults to `state_store = state_store or
    InMemoryStateStore()`; `_discover_artifact_store()` still falls back to the playground state store's
    `artifact_store` (which is `PlaygroundArtifactStore`). Both are untouched. In `inline` mode
    `rich_output/nodes.py` skips the store write entirely (`if delivery in {"both", "artifact"}`) and the
    `artifact_stored` event is gated by `emit=emit_visible` only when the store write happens — so in
    `inline` no UI bytes are written and no UI `artifact_stored` fires. The `artifact_chunk` SSE path
    (`_emit_component_artifact`) still runs for `inline`/`both` (line `if emit_visible and delivery in
    {"inline", "both"}`).

### Assumptions

- **The live `/chat/stream` UI `artifact_stored` events do not carry `extra["message_id"]`**, so the
  resolved `message_id` equals `stream_message_id` (the active agent message). Verified that the planner
  proxy (`artifact_handling.py::_emit_artifact_stored_event`) builds `extra` with `artifact_id`,
  `mime_type`, `size_bytes`, `artifact_filename`, and `source` only — no `message_id`. So the resolved
  value comes from `default_message_id=stream_message_id`. If a future change adds a per-event
  `message_id`, `setdefault` + the resolution order means that explicit value would be honored, which is
  the intended/desired behavior.

- **Frontend render-slot placement is out of scope for this backend phase.** The exit criteria are about
  the backend emitting a frame that *carries* `message_id`/`default_message_id`; wiring the frontend to
  consume them (the events store / chat-stream service) is not part of this phase and was not modified.

- **The `source` namespace key is `"namespace"`** (confirmed in both the planner proxy event extra and
  the `rich_output` write descriptor), matching the constant string the phase specifies.

### Deviations from Plan

- The Required-Code snippet used `frame.setdefault("message_id", default_message_id)` /
  `frame.setdefault("default_message_id", default_message_id)`. I instead inject onto the `payload` dict
  (which is the variable name in the current code; there is no `frame` dict at that point — `frame` is the
  serialized bytes returned by `format_sse`) and use the resolved `message_id` variable rather than the
  raw `default_message_id` parameter. This is what the phase's own "match the exact stream-chunk
  frame/field shape" instruction requires, and it preserves an explicit `extra["message_id"]` if one is
  ever present. Field names emitted (`message_id`, `default_message_id`) and the gate
  (`source.namespace == "penguiflow_ui_component"`) are exactly as specified. Functionally identical to
  the snippet on the live path.

- Added unit tests (the phase's verification command targets `-k "playground or artifact_stored or
  artifact"` and the file already had a `TestSSEEventFrames` suite for `_event_frame`, so adding coverage
  here matches the established pattern). The phase did not explicitly request new tests, but they lock in
  the new behavior and the backward-compat guard.

### Potential Risks & Reviewer Attention Points

- **Single behavioral line is small and surgical.** The risk surface is the gate condition. Reviewers
  should confirm: (a) the `isinstance(source, Mapping)` guard prevents the string-source regression
  (covered by the augmented `test_artifact_stored_event_frame`), and (b) only the
  `penguiflow_ui_component` namespace is affected — binary/MCP artifacts (e.g. `tableau`) get no
  injection (covered by `test_artifact_stored_non_ui_namespace_no_message_id`).
- **`default_message_id` field on the frame is new.** The stream-chunk path only emits `message_id`; this
  phase (per exit criteria) emits both `message_id` and `default_message_id` on the UI `artifact_stored`
  frame. If the frontend treats unknown keys strictly, the extra `default_message_id` field could be a
  consideration — but per the exit criteria it is intentional and frontends generally ignore unknown SSE
  payload keys. The follow-up frontend consumer phase (if any) should decide which of the two it reads.
- **No happy-path change beyond the new flag surfacing.** The full suite (2861 passed, 7 skipped) and the
  scoped suite (429 passed) both pass unchanged, confirming the `inline` default path is byte-for-byte
  unaffected.

### Files Modified

- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground.py` (modified — Step 2
  message-id injection in `_event_frame` `artifact_stored` branch)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/test_playground_phase3.py` (modified — 3 new
  tests + 2 assertions on an existing test)
