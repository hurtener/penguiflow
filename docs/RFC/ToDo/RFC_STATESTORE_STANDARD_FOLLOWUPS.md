# RFC: StateStore Standard Follow-Ups (Jan 2026)

> **Status:** ToDo  
> **Owner:** PenguiFlow core  
> **Audience:** Core maintainers + downstream platform teams  
> **Last Updated:** January 2026  

This RFC enumerates the follow-ups required to make PenguiFlow's StateStore surface fully consistent, explicitly specified, and production-grade across runtime subsystems (core runtime, sessions, planner, artifacts, and distributed/A2A hooks).

It is intentionally a backlog RFC: it lists *what we should fix/standardize next* (and why), without committing to a single implementation sequence.

---

## Background

PenguiFlow v2.11.x uses a duck-typed `StateStore` Protocol plus optional capability protocols (planner pause, memory, sessions, trajectories, planner events, artifacts). The system is intentionally flexible, but over time several mismatches emerged between:

- The *documented contracts* (`docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md`)
- The *typed surface* (`penguiflow/state/protocol.py`)
- The *actual runtime expectations* (core runtime vs sessions vs planner)

Downstream teams implementing custom backends need a single source of truth that is accurate and stable. This RFC captures the remaining work to reach that bar.

---

## Goals

- Make **type signatures match runtime behavior**, especially on "not found" and idempotency semantics.
- Define **stable event schemas** (not just string discriminators) for persisted data.
- Ensure **runtime error handling is predictable** (what is best-effort vs what can propagate).
- Provide **first-class performance patterns** (batching, backpressure, TTLs) without each downstream team reinventing them.
- Establish a **contract-test harness** so custom stores can be validated automatically in CI.

---

## Non-Goals

- Building a fully-managed hosted StateStore service.
- Committing to one storage backend (Postgres vs Redis vs DynamoDB, etc.).
- Solving A2A persistence semantics while A2A is being reworked (see “A2A Status” below).

---

## A2A Status (Jan 2026)

The A2A subsystem is being reworked and is currently out of spec. StateStore support for A2A remote bindings should be treated as **experimental** until the A2A spec is finalized.

This RFC tracks StateStore follow-ups that are independent of the A2A redesign, and keeps A2A-related storage items in a dedicated subsection.

---

## Current Gaps / Follow-Ups

### 1) Protocol typing mismatch: `load_planner_state()`

**Issue:** `SupportsPlannerState.load_planner_state()` is typed as returning `dict[str, Any]`, but the runtime treats `None` as the clean “missing token” signal.

**Impact:** Downstream stores that return `{}` for missing tokens can cause confusing runtime errors (e.g., `KeyError: 'trajectory'`) instead of a clear “token missing/expired” behavior.

**Proposed Fix:**
- Update `penguiflow/state/protocol.py`:
  - `load_planner_state(self, token: str) -> dict[str, Any] | None`
- Update planner resume paths to treat `{}` as missing defensively (migration safety).
- Update docs and contract tests.

**Migration:** Backward compatible by accepting both `{}` and `None` during a transition period.

---

### 2) Deterministic ordering for `StoredEvent`

**Issue:** `StoredEvent` does not include a durable monotonic identifier (`id`/`seq`). The doc recommends a secondary ordering for ties, but the required model does not carry that field.

**Impact:** High-throughput traces can emit multiple events with the same timestamp, causing nondeterministic ordering across DB implementations and replay tools.

**Proposed Fix Options:**
- Add `event_id: str` (UUID) and/or `seq: int` to `StoredEvent` (optional for backwards compatibility).
- Alternatively, standardize that stores must persist an internal insertion id and order by `(ts, insertion_id)` on reads.

**Migration:** Allow missing `event_id/seq` and treat ordering as best-effort for older records.

---

### 3) Explicit schemas for persisted event payloads

**Issue:** `StoredEvent.payload` is an untyped `Mapping[str, Any]`. Over time, multiple subsystems added fields opportunistically.

**Impact:** Downstream storage, analytics, and replay pipelines lack a stable schema contract.

**Proposed Fix:**
- Define Pydantic models (or TypedDicts) for:
  - Core runtime events (`node_start`, `node_success`, …)
  - Session pseudo-events (`session.task`, `session.update`, `session.steering`)
- Provide a registry: `kind -> schema`, plus a helper that validates (optionally) before persisting.

**Migration:** Validation should be opt-in / “warn-only” initially.

---

### 4) Clarify and standardize error-handling policy across subsystems

**Issue:** Core runtime wraps `save_event`/`save_remote_binding` best-effort, but sessions and other paths can await store calls directly (or fire-and-forget tasks without exception capture).

**Impact:** A failing store can break interactive features and appear as silent hangs if unhandled tasks fail.

**Proposed Fix:**
- Document and enforce a single policy:
  - “StateStore must not throw” for runtime hot paths (sessions/planner).
  - Or: wrap all StateStore calls at integration boundaries consistently.
- Add internal “safe call” helpers (`_safe_store_call`) and use them everywhere.
- Add telemetry for failures (rate, latency, timeouts) with structured tags.

**Migration:** Backward compatible, mostly internal refactors.

---

### 5) Bulk write capability (optional)

**Issue:** High-volume event streams benefit from batching, but the protocol only defines per-event writes.

**Proposed Fix:**
- Add optional capability protocol:
  - `SupportsBulkEvents.save_events(events: Sequence[StoredEvent]) -> None`
- Provide core helper that feature-detects bulk support and falls back to single writes.

**Migration:** Optional capability; no breaking change.

---

### 6) First-class retention/cleanup guidance and hooks

**Issue:** The doc includes schema suggestions, but retention policies are not standardized (events, pauses, artifacts, planner events, session updates).

**Proposed Fix:**
- Standardize recommended TTLs and cleanup strategies per table/key type.
- Add optional hooks for stores to expose retention configuration and report storage pressure.
- Provide reference “cleanup job” scripts or migrations.

---

### 7) Contract tests as a supported artifact

**Issue:** Downstream teams have to guess whether their store matches runtime semantics.

**Proposed Fix:**
- Add a small `penguiflow.state.contract_tests` module (pytest helpers) that can be imported by downstream repos:
  - Tests for idempotency, cursor semantics, missing-token behavior, ordering, etc.
- Keep it stable and versioned with the protocol.

---

### 8) Session audit-log adapter semantics

**Issue:** `StateStoreSessionAdapter` persists session state under `trace_id="session:{session_id}"`. This is useful but somewhat implicit.

**Proposed Fix:**
- Make the trace-id scheme a named constant and document it as part of the observability contract.
- Consider making session persistence a first-class “event family” instead of overloading the audit log.

---

### 9) A2A remote binding persistence (blocked on A2A redesign)

**Issue:** `RemoteBinding` persistence exists, but the lifecycle semantics (upsert keys, stale binding cleanup, multi-worker concurrency) are not fully specified.

**Proposed Fix (post-A2A-spec):**
- Define binding lifecycle + TTL semantics.
- Define the keying strategy (trace_id/context_id/task_id vs other).
- Define “handoff” behavior for retries/failover.

---

## Proposed Rollout Plan (Suggested)

1) Fix protocol typing mismatch (`load_planner_state`) + docs + contract tests.
2) Standardize error handling across subsystems + add telemetry.
3) Add deterministic ordering guidance (and optionally `seq`/`event_id`).
4) Add schemas for persisted events (warn-only validation first).
5) Add optional bulk write capability.
6) Revisit session audit-log adapter / naming once above is stable.
7) After A2A redesign, finalize `RemoteBinding` persistence spec.

---

## Open Questions

- Should we make “must not throw” a hard requirement, or should runtime wrap everything to enforce best-effort semantics?
- Do we want `StoredEvent` to become a Pydantic model (easier migration + schema evolution) instead of a dataclass?
- Is it worth introducing a “StateStore version handshake” to let stores advertise supported capabilities/features?
- How do we best bound storage for high-volume streaming events (`PlannerEvent` stream chunks) without losing usability for the Playground UI?
