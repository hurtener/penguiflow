# Background Tasks Follow-Ups (Visibility + Artifacts + Patch Tools)

## Context / Symptoms Observed
- Background tasks run (some succeed/fail), but terminal is silent during background execution.
- Playground Tasks card only shows foreground tasks.
- No notifications for individual tasks or task groups.
- Some background tasks fail when they produce artifacts (e.g., `gather_data_from_genie`).
- `tasks.apply_group` reports applying `0` patches even when group shows pending patches; subsequent patch flows appear broken.

## Issue 1 — Background task failures when artifacts exist (context patch serialization bug)
**What you see**
- Background tasks that use tools producing artifacts fail, even though the tool ran successfully.
- Error details aren’t visible in terminal (see Issue 3), but task ends `FAILED`.

**Root cause (confirmed in code)**
- `ReactPlanner` stores artifacts in `PlannerFinish.metadata["artifacts"]` as a **dict**: `{"tool_name": <artifact_payload>, ...}`.
  - Source: `penguiflow/planner/react.py` (`_finish()` uses `"artifacts": dict(trajectory.artifacts)`).
- Background task pipeline builds a `ContextPatch` by doing `list(metadata["artifacts"])`, which converts a dict into a list of **keys**:
  - Example: `["gather_data_from_genie"]`.
  - Source: `penguiflow/sessions/planner.py` (`_build_context_patch()` uses `artifacts=list(artifacts or [])`).
- `ContextPatch.artifacts` is typed as `list[dict[str, Any]]`, so this causes validation/runtime failures and the task is marked failed.
  - Source: `penguiflow/sessions/models.py` (`ContextPatch.artifacts`).

**Target files**
- `penguiflow/sessions/planner.py`
- (Secondary for context) `penguiflow/planner/react.py`, `penguiflow/sessions/models.py`

**Direction of fix**
- Change `penguiflow/sessions/planner.py:_build_context_patch()` to normalize `metadata["artifacts"]`:
  - If it’s a mapping, convert it to a list of dictionaries, e.g.:
    - `[{ "tool": tool_name, "fields": payload } ...]`
    - where `payload` is the *artifact-marked fields* collected by the planner, and may include compact `ArtifactRef` objects (preferred) rather than raw content.
  - If it’s already a list, keep as-is.
  - If unknown type, coerce to empty list (or include under `facts` instead).
- **Important**: do **not** “inline” binary/large artifacts into patches.
  - PenguiFlow already supports out-of-band storage via `ArtifactStore` and `ArtifactRef` (the intended mechanism to avoid bloating LLM context).
  - The background/foreground behavior should be seamless: tools should keep returning `ArtifactRef` in observations, and the patch conversion should preserve those references (serialize refs, not bytes).
- Add a regression test that runs a background task pipeline with `metadata={"artifacts": {"tool": {"x": 1}}}` and asserts:
  - task ends `COMPLETE`
  - patch creation succeeds
  - patch contains structured `artifacts` (not a list of strings).

---

## Issue 2 — `tasks.*_group` tool wrappers return the wrong fields (schema mismatch)
**What you see**
- Foreground agent says: “apply_group returned 0 patches” even though group contains patches.
- Similar inconsistencies likely exist for `tasks.seal_group` / `tasks.cancel_group`.

**Root cause (confirmed in code)**
- `InProcessTaskService` returns dict payloads keyed as `{"success": ..., "applied_count": ..., "cancelled_count": ...}`
  - Source: `penguiflow/sessions/task_service.py` (`apply_group()`, `cancel_group()`, etc).
- The `tasks.*` tool wrappers expect different keys: `{"ok": ..., "applied_patch_count": ...}`
  - Source: `penguiflow/sessions/task_tools.py` (`tasks_apply_group()`, `tasks_cancel_group()`, `tasks_seal_group()`).
- Result: tools report `ok=False` and `applied_patch_count=0` even when the operation succeeded.

**Target files**
- `penguiflow/sessions/task_tools.py`
- `penguiflow/sessions/task_service.py`

**Direction of fix**

- Standardize TaskService return schemas:
  - Make `InProcessTaskService` return `{"ok": bool, ...}` and the exact fields used by tool models.
  - Prefer typed returns (Pydantic models) over ad-hoc dicts to prevent drift.
- Add tests for group APIs ensuring:
  - `tasks.apply_group` returns `ok=True` and correct `applied_patch_count`.
  - `tasks.cancel_group` returns `ok=True` and correct counts.
  - `tasks.seal_group` returns `ok=True`.

---

## Issue 3 — No terminal visibility into background execution (telemetry is NoOp)
**What you see**
- Background tasks appear to “do nothing” from the terminal/logging perspective.
- Failures happen “silently” unless surfaced into UI state (and even that can be missing, see Issue 4).

**Root cause (confirmed in code)**
- `StreamingSession` emits `TaskTelemetryEvent` for spawn/complete/failure, but default `TaskTelemetrySink` is `NoOpTaskTelemetrySink`.
  - Source: `penguiflow/sessions/session.py` uses `telemetry_sink or NoOpTaskTelemetrySink()`.
  - Source: `penguiflow/sessions/telemetry.py`.
- Exceptions in background tasks are handled (task status updated + ERROR StateUpdate), but not necessarily logged to a logger.
  - Source: `penguiflow/sessions/session.py` `except Exception as exc:` path.

**Target files**
- `penguiflow/sessions/telemetry.py`
- `penguiflow/sessions/session.py`
- Template/spec orchestrator fallbacks (to opt-in to logging sink):
  - `penguiflow/templates/new/*/src/__package_name__/orchestrator.py.jinja`
  - `test_generation/background-tasks-agent/src/background_tasks_agent/orchestrator.py`

**Direction of fix**
- Implement a `LoggingTaskTelemetrySink` (or similar) in `penguiflow/sessions/telemetry.py`:
  - Log `task_spawned`, `task_completed`, `task_failed`, group completion/failure.
  - Include `session_id`, `task_id`, `group_id` (if available), `trace_id`, and `error`.
- In fallback `SessionManager(...)` creation, pass `telemetry_sink=LoggingTaskTelemetrySink(logger=...)`.
- Additionally log exceptions in `StreamingSession._execute_task` failure path (at least `logger.exception(...)`).
- Add tests for telemetry sink invocation (unit test with a fake sink capturing events).

---

## Issue 4 — Tasks card + notifications show only foreground (split SessionManager instances)
**What you see**
- Playground `/session/stream` and `/tasks` endpoints show only foreground tasks.
- Notifications about background completion/patch approval do not appear.

**Likely root cause (from wiring)**
- Playground UI reads tasks via `/tasks?session_id=...` and listens via `/session/stream` to the Playground server’s **SessionManager** instance.
  - Source: `penguiflow/cli/playground.py` creates `session_manager = SessionManager(...)` and serves `/tasks` and `/session/stream`.
  - Source: `penguiflow/cli/playground_ui/src/lib/services/session-stream.ts`.
- Generated agents now create an in-process fallback `SessionManager` inside the orchestrator when none is provided.
  - If the Playground host does not provide a shared `session_manager` into the agent/orchestrator, the agent’s background tasks run on a **different SessionManager**, so UI endpoints never see them.

**Target files**
- Runtime/host integration:
  - `penguiflow/cli/playground.py`
  - `penguiflow/agui_adapter/penguiflow.py`
  - Agent wrapper loading path (where orchestrator gets constructed)
- Agent/orchestrator side:
  - template orchestrators already accept `session_manager`/`task_service` but need host to pass them.

**Direction of fix**
- Ensure there is exactly **one** SessionManager per process, shared by:
  - UI endpoints (`/tasks`, `/session/stream`)
  - AG-UI adapter (foreground registration)
  - Orchestrator background task service (spawn/list/apply/notifications)
- Prefer dependency injection over “UI injection”:
  - If running under Playground server, pass its `session_manager` into the orchestrator constructor if supported.
  - Alternatively (if constructor injection isn’t feasible), standardize a tool_context key (e.g. `session_manager`) and have orchestrator prefer it over creating a fallback.
- Acceptance criteria:
  - Starting background tasks makes them appear in Tasks card immediately.
  - Completion triggers NOTIFICATION updates and (for HUMAN_GATED) context-patch checkpoints.

---

## Plan (Recommended Execution Order)
1. Fix artifact serialization in background patches (`penguiflow/sessions/planner.py`) and add regression tests.
2. Fix group tool wrapper/schema mismatch (`penguiflow/sessions/task_tools.py` + `penguiflow/sessions/task_service.py`) and add tests.
3. Add background task logging:
   - Introduce `LoggingTaskTelemetrySink`
   - Wire it into fallback `SessionManager` creation
   - Add failure-path logging in `StreamingSession._execute_task`.
4. Unify SessionManager usage so UI + agent share the same instance in Playground/AG-UI contexts:
   - Prefer DI: pass `session_manager` into orchestrator when hosted.
   - Add an integration test that verifies background task appears via `/tasks` and emits `/session/stream` updates.

## Notes / Quick Checks
- If you see artifact-related failures, check for errors originating in `ContextPatch(...)` creation during background completion.
- If `tasks.apply_group` returns `ok=False` with `0` patches, verify whether the underlying service returned `success/applied_count` instead of `ok/applied_patch_count`.
