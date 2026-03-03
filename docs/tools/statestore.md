# State store

## What it is / when to use it

`StateStore` is the persistence interface for PenguiFlow’s runtime, planner, and (optionally) session layer.

You need a real StateStore in production when you want:

- durable audit/replay of traces (event history),
- distributed pause/resume (HITL/OAuth) across multiple workers,
- memory persistence across restarts (optional),
- durable background tasks / steering (if you use the sessions layer).

## Non-goals / boundaries

- `StateStore` does not define a storage backend (Postgres, Redis, etc. are up to you).
- `StateStore` is not a queue; it stores events and state, it does not schedule execution.
- Access control is your responsibility (tenant scoping, encryption, retention).

## Contract surface

The core protocol lives in:

- `penguiflow.state.protocol.StateStore`

### Required methods (minimum)

Every implementation must provide:

- `save_event(event: StoredEvent) -> None`
- `load_history(trace_id: str) -> Sequence[StoredEvent]`
- `save_remote_binding(binding: RemoteBinding) -> None`

These enable trace history, audit/replay, and distributed execution bindings.

### Optional capabilities (detected by duck-typing)

Planner pause/resume:

- `save_planner_state(token: str, payload: dict) -> None`
- `load_planner_state(token: str) -> dict | None`

Short-term memory persistence:

- `save_memory_state(key: str, state: dict) -> None`
- `load_memory_state(key: str) -> dict | None`

Session KV facade (tool `ctx.kv`):

- Tools can persist intermediate state without calling the StateStore directly:
  - `await ctx.kv.set("state", {"phase": "queried"})`
- Backed by the same optional memory persistence methods above (`save_memory_state` / `load_memory_state`).
- Reserved keyspace (do not use for your own app data):
  - session-scoped (default, no TTL): `kv:v1:{tenant}:{user}:{session}:session:{namespace}:{key}`
  - task-scoped (opt-in, fixed TTL=3600s): `kv:v1:{tenant}:{user}:{session}:task:{task_id}:{namespace}:{key}`
- Observability:
  - each KV mutation emits planner events (`kv_set`, `kv_patch`, etc.) and is projected into `StateUpdate(update_type=CHECKPOINT)`
- Consistency:
  - best-effort multi-writer (no CAS) when implemented via `save_memory_state` / `load_memory_state`

Planner event storage:

- `save_planner_event(trace_id: str, event: PlannerEvent) -> None`
- `list_planner_events(trace_id: str) -> list[PlannerEvent]`

Artifacts:

- expose `artifact_store` or implement `ArtifactStore` (including the `list` method) so the planner can discover it (`discover_artifact_store`). Tool developers access artifacts via `ctx.artifacts` (a `ScopedArtifacts` facade); the raw `ArtifactStore` is plumbing.

Sessions/tasks/steering/trajectories:

- see `penguiflow.state.protocol.SupportsTasks`, `SupportsSteering`, `SupportsTrajectories`

## Operational defaults

- Use a durable backend (PostgreSQL is the common baseline).
- Make `save_event` idempotent (retries can emit duplicates).
- Set TTL/retention for pause tokens and artifacts consistent with your UX.
- For multi-tenant: scope keys by tenant/user/session and enforce access at the storage layer.

## Failure modes & recovery

### Pause tokens invalid after restart (`KeyError`)

**Likely causes**

- pause records were only in-memory and the worker restarted
- `save_planner_state` / `load_planner_state` is not implemented
- token TTL is too short for your UX

**Fix**

- implement planner state persistence methods
- align TTL with UI flows and OAuth callbacks

### Memory doesn’t persist

**Likely causes**

- store does not implement `save_memory_state` / `load_memory_state`

**Fix**

- implement memory persistence (or accept that STM is per-process)

## Observability

Record at minimum:

- state store operation latency and error rates (save/load)
- pause save/load failures (they indicate broken HITL/OAuth flows)
- event write volume by trace/session

If you persist planner events, you can replay/debug from stored telemetry.

## Security / multi-tenancy notes

- Treat stored events as sensitive (they can contain user input and tool observations).
- Redact secrets before storing events if your tools might surface them.
- Use per-tenant partitioning or explicit scoping keys to prevent cross-tenant reads.

## Runnable example: development store

For local development and the playground, the repo includes an in-memory store:

```python
from penguiflow.state import InMemoryStateStore

store = InMemoryStateStore()
```

## Production implementations (guidance)

In production you typically implement the protocol on top of PostgreSQL, Redis, or another durable store.

See also:

- `docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md` (implementation spec)
- `docs/tools/statestore-guide.md` (long-form internal guide)

## Troubleshooting checklist

- **Resume tokens invalid in prod**: you need `save_planner_state`/`load_planner_state` and appropriate TTL.
- **History missing**: ensure `save_event` is wired for all workers and `trace_id` partitioning is correct.
- **Cross-tenant leaks**: ensure keys and queries are tenant-scoped and access is enforced at read time.
