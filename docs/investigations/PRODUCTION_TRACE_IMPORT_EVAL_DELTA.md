# Importing production traces into PenguiFlow evals: what `run_import_eval.py` does, and what belongs upstream

**Status:** understanding-mode investigation. No code changed by this document.
**Subject:** `run_import_eval.py` (repo root, untracked, 705 lines, never committed).
**Counterpart:** the Agentiv platform backend at `/home/german/projects/ai-platform-backend` (referred to below as **PB**).
**Verified against:** penguiflow `3.9.0` (`pyproject.toml:7`); PB at its current checkout.

---

## Why this document exists

`run_import_eval.py` pulls real production agent traces out of PB over HTTP and scores them
with a PenguiFlow metric — no agent re-run, no direct database access. In theory it should be
a thin driver over `penguiflow.evals`. In practice it carries a substantial **delta**: glue
code that exists only because the eval API assumes a live local `StateStore` and a
locally-produced dataset, while PB exposes traces over HTTP with no global listing.

That delta had never been written down, so it was impossible to say which parts are
legitimate deployment glue, which are workarounds for real library gaps that should be fixed
upstream, and which are workarounds for PB limitations that penguiflow cannot fix at all.
This document answers that, and records the defects found along the way.

The framing gap is worth naming up front. `docs/proposals/RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md:14`
states the goal as turning **production traces** into repeatable datasets, and cites
LangSmith's "create datasets from production traces" as the model. But **no document in the
repo describes importing traces from a remote backend.** Every documented path
(`docs/cli/eval-command.md`, `docs/cli/react-planner-evals.md`, `docs/cli/playground-evals.md`)
assumes a local `StateStore` — either the Playground's, or one created by `eval collect`
re-running the agent. `run_import_eval.py` is the first and only attempt at the RFC's stated
goal, and it lives outside the library entirely.

---

## 1. What the script actually does

Nine stages, top to bottom:

1. **Resolve the PB base URL** — `_resolve_pb_url` (`:481`). Precedence: `--pb-url` >
   `PB_URL_<ENV>` env var > the hardcoded `PB_ENVS` map (`:98`). A surviving `<` character
   means "placeholder, not configured" and aborts (`:487`).
2. **Resolve auth** — `_resolve_auth_headers` (`:466`). An explicit `__Host-databricksapps`
   cookie (`--cookie`/`--cookie-file`/`PB_COOKIE`) wins if present; otherwise a Databricks
   OAuth U2M token is shelled out of `databricks auth token` (`_oauth_token_from_cli`, `:446`)
   and sent as `Authorization: Bearer`.
3. **Enumerate sessions** — three mutually exclusive modes:
   - `--agent-id`: paginated `GET /v2/users/{uid}/agents/{aid}` (`list_agent_sessions`, `:165`),
     optionally fanned across many users via `--user-id`/`--users-file`.
   - `--all-sessions`: `GET /sessions` (user-scoped, all agents mixed).
   - `--session-id`: explicit, repeatable.
4. **Fetch traces per session** — controlled by `--source`:
   - `trajectories`: `GET /state-store/trajectories?session_id[eq]=…` (`:144`).
   - `messages`: `GET /sessions/{id}/messages` (`:128`), reading each assistant turn's
     `metadata` as a partial trajectory and pairing it with the preceding user turn.
   - `auto` (default): trajectories first, falling back to messages **per session**.
   `_with_retry` (`:133`) retries transient timeouts/transport errors only — never auth.
5. **Rehydrate** — `Trajectory.from_serialised` on each payload (`_rehydrate`, `:224`).
   Malformed payloads are reported to stderr and skipped rather than aborting the import.
6. **Build canonical rows** — `collect_trace_rows` fed through the `_PrefetchedStore` shim
   (`:202`), then post-patched three ways (`build_dataset_rows`, `:314`): `outputs.final` is
   filled in, `trajectory_full` is replaced with the untouched raw payload, and `split` falls
   back to `--split`.
7. **Write the bundle** — `dataset.jsonl` + `manifest.json` (`:627-634`).
8. **Score** — `run_harness_eval` with a no-re-run `run_one` (`_make_run_one`, `:357`) that
   simply returns the stored answer and raw trajectory. Metric is the built-in `smoke_metric`
   or a user-supplied `--metric-spec`.
9. **Report** — `results.csv` (`_write_csv`, `:401`), which expands a strict
   `k=0|k=1|…` feedback string into one column per criterion, plus a printed mean.

**Reused from the library** (`:87-90`): `collect_trace_rows`, `run_harness_eval`,
`wrap_metric`, `resolve_callable`, `extract_node_sequence`, `Trajectory.from_serialised`.

---

## 2. The delta, in three buckets

### Bucket A — deployment glue. Stays in the script.

| Script symbol | Why it exists | Verdict |
|---|---|---|
| `PB_ENVS`, `_resolve_pb_url` (`:98`, `:481`) | PB has per-environment hostnames | Stays — deployment config |
| `_oauth_token_from_cli`, `_resolve_auth_headers` (`:446`, `:466`) | Databricks Apps front door | Stays — penguiflow should not learn about Databricks |
| `AuthWallError`, `_get_json` (`:107`, `:111`) | PB returns an HTML login page instead of a JSON error | Stays |
| `list_sessions`, `list_agent_sessions`, `_derive_user_id`, `_resolve_user_ids` | PB has no global trace listing, so sessions must be enumerated first | Stays — but see Bucket C |
| `_with_retry` (`:133`) | Transient network errors over a long import | Stays |

The script's docstring (`:23-26`) is **correct** that `TraceSelector`/`resolve_trace_refs`
cannot be used here. `resolve_trace_refs` hard-requires a `list_trace_refs` method on the
store and raises `TypeError("StateStore missing list_trace_refs required for global dataset
export")` without it (`penguiflow/evals/api.py:318-320`). PB has nothing to back that method
with, so `TraceSelector` genuinely has nothing to select over for imported production traces.

### Bucket B — genuine `penguiflow.evals` gaps

Route map, ordered by priority. B2 and B1 are **done**; B3/B4 are scoped proposals awaiting a
design decision; B5/B6 are reclassified as conveniences rather than genuine gaps.

### Fixed

| # | Gap | Fix |
|---|---|---|
| **B2** | `run_harness_eval` never awaited the metric — `runner.py:142` called `metric(...)` bare, so an async metric returned a coroutine that `_as_score_payload` (`runner.py:24-43`) matched against none of its branches and scored `0.0`, silently, with only a "coroutine was never awaited" RuntimeWarning | Wrapped the call in the module's existing `_maybe_await` (`runner.py:18-21`), matching how `run_one` is already awaited two lines above. Sync metrics are unaffected. Regression test: `tests/evals/test_slice0_runner.py::test_run_harness_eval_awaits_async_metric`. |
| **B1** | `outputs.final` hardcoded `None` (`export.py:84`) | Added `_infer_final(trajectory)`, which reverse-scans steps for `action.is_terminal()` and returns `action.answer_text()` — reverse so reflection/revision runs yield the final answer, not an intermediate one. `outputs.final` is now declared in `redaction.fields_included`, which matters because it is free-text model output. Three regression tests in `tests/evals/test_slice0_export.py` cover the modern `args.answer` shape, the legacy `args.raw_answer` shape, and the no-terminal-step case. Inert on the ordinary answer path until **B7** supplied an answer for it to read. |
| **B7** | The final answer was never written into the trajectory, so `outputs.final` stayed `None` for real traces even after B1 | `Trajectory.final_answer`, recorded by `_finish` and preferred by `_infer_final`. See the dedicated section below. |

**Correction to an earlier version of this document.** The B2 entry previously claimed the bug
"affects the sanctioned CLI path too". **That was wrong.** There are three metric-invocation
paths, and only one lacked the await:

| Path | Awaits? |
|---|---|
| `evaluate_dataset` → `api.py:1360` (what `penguiflow eval evaluate` uses) | Yes, always did |
| Playground eval → `penguiflow/cli/playground.py:2701-2709` | Yes, always did |
| `run_harness_eval` → `runner.py:142` | **No** — this was B2 |

B2 was therefore **unreachable through the CLI**. `run_harness_eval` is exported
(`penguiflow/evals/__init__.py:38-40`) but reached only via `run_eval_workflow` →
`run_manual_sweep`, neither of which is CLI-exposed or documented; outside the library its only
callers are `tests/evals/test_slice0_*` and `run_import_eval.py:638`. The structural cause is that
`evaluate_dataset` and `run_manual_sweep`/`run_eval_workflow` are two parallel implementations of
baseline + candidates + threshold gating, and the CLI standardised on the former. B2 was real, but
its blast radius was the programmatic harness API — in practice, this import script.

### B7 — the final answer was not persisted in the trajectory at all — **Fixed**

**Found by running the enterprise v2 CLI smoke test after the B1 fix.** B1 was correct but inert on
the ordinary answer path, because there was no terminal step for it to read, so `outputs.final`
stayed `None` for real traces.

`_handle_final_response` builds the answer and hands it to `planner._finish(...)`
(`penguiflow/planner/react_runtime.py:1591-1611`). It **never appends a `TrajectoryStep`** for the
terminal action. The answer therefore lives on `PlannerFinish.payload` — the run *result* — and
never enters `trajectory.steps`, so it is absent from `serialise()` and from anything the exporter
can see.

Verified against 4 freshly collected enterprise-v2 traces: every trajectory ended on a tool-call
step (`triage_query` → `analyze_documents`), and `outputs.final` was `None` on all 4. The answer
is not recoverable from the events either — the `finish` planner event's `extra` carries only
`answer_action_seq`, `cost` and `reason`; the answer text exists solely as `llm_stream_chunk`
fragments.

So `_infer_final` populates `outputs.final` only when a terminal step actually exists:

| Situation | Terminal step present? |
|---|---|
| Reflection/revision ran — `react_runtime.py:1554` appends the revised action, and `request_revision` returns `normalize_action(raw)` (`llm.py:1313`), i.e. `final_response` | **Yes** |
| Trajectory deserialised from a payload that contains a terminal step (historical data, other planners) | **Yes** |
| Ordinary non-reflection answer path | **No** — this is the common case |

The enterprise v2 example runs with `REFLECTION_ENABLED=false`, which is exactly why the smoke
test produced no answers.

**The fix.** `Trajectory` gained an explicit `final_answer: str | None` field
(`penguiflow/planner/trajectory.py`), emitted by `serialise()` and read back with
`payload.get("final_answer")` so every pre-existing stored payload stays valid. `ReactPlanner._finish`
(`penguiflow/planner/react.py`) records it, **only for `reason == "answer_complete"`**, via a small
`_answer_from_payload` helper mirroring `PlannerAction.answer_text()` precedence. Terminations that
deliberately produce a message for the user — currently just the guardrail STOP path
(`react_runtime.py`) — pass it explicitly as `user_facing_answer=`, a parameter tool data can never
reach. `_finish` is the single point all **8** termination sites
funnel through, it runs after the iteration loop so it cannot affect `_max_iters` or any budget, and
persistence fires afterwards against the live trajectory object — so the answer lands in the saved
record. `_infer_final` now prefers the recorded answer and keeps its terminal-step scan as a
fallback for reflection runs and imported legacy traces.

**Why not append a terminal `TrajectoryStep`** — the intuitive fix, rejected after a blast-radius
review found six real side effects:

1. `Trajectory.compress()` derives `last_observation`/`last_output_digest` from the last step; a
   terminal step has no observation, so rolling-summary memory would **silently** lose the last tool
   result. This was the decisive one — a quality regression with no failing test.
2. Reflection already appends a terminal revision step (`react_runtime.py:1554`), so a run would end
   with two or more consecutive terminal steps.
3. `PlannerFinish.metadata["step_count"]` would shift by one — a public contract change.
4. The answer would be duplicated a third time per run, with no truncation anywhere.
5. The reflection revision prompt's "original thought" source would change (`llm.py:1288`).
6. Existing step-count assertions would break.

The field approach touches none of these. Verified on the same 4 enterprise-v2 traces that
originally exposed B7: all four now carry answers of 1462–1888 characters, while the trace shape is
unchanged — still 2 steps, still `['triage_query', 'analyze_documents']`, so no route-based metric
shifts.

**Defect found and fixed after the first B7 landing — read this before touching `_finish`.**
The initial implementation extracted `answer`/`raw_answer` from *every* finish payload regardless of
reason, on the stated assumption that the non-answer paths "pass a bare `last_observation` that has
no such key". **That assumption was wrong.** Three sites pass the last raw tool observation straight
through as the payload — `react_runtime.py` deadline, hop-budget exhaustion, and iteration limit —
and tool output commonly contains an `answer` key of its own (the repo's own `EchoOut` test model is
exactly `{"answer": str}`).

Reproduced: a planner looping on a tool returning `{"answer": "INTERNAL TOOL VALUE"}` until it hit
`max_iters` finished `no_path` and recorded that string as the run's answer. The exported row was
*indistinguishable from a genuine success*, because `_infer_status` (`export.py:44-50`) only inspects
step errors and never consults the finish reason:

```
planner reason : no_path
outputs.status : ok
outputs.final  : 'INTERNAL TOOL VALUE - not a user answer'
```

An eval dataset would have silently scored fabricated answers as real — the same class of silent
corruption as B2. Fixed by the reason gate plus the explicit `user_facing_answer` parameter described
above; regression tests live in `tests/planner/test_persistence.py` (tool-output leak) and
`tests/test_guardrails_integration.py` (the intentional-failure-answer case the gate must not
discard).

**Follow-up — `outputs.status` ignored the finish reason — now fixed.** `_infer_status` derived
status purely from step errors, so a run that exhausted its budget or iteration limit without any
step erroring still exported `status: "ok"`. This is what let the leak above hide in plain sight.

`Trajectory` now carries `finish_reason`, set unconditionally in `_finish` (the reason is always
planner-authored, so unlike the answer it needs no gating), and `_infer_status` treats any recorded
reason other than `answer_complete` as `error`. Trajectories stored before this change have no
reason and keep the previous behaviour, so existing datasets are unaffected.

The reason is surfaced three ways: `trajectory.finish_reason` in the row (next to `split`/`tags`,
and declared in `redaction.fields_included`), automatically inside `trajectory_full` since it is now
part of `serialise()`, and — as before — in `events.planner_events[*].extra.reason`. Metrics can
read `pred_trace["finish_reason"]`; note `run_import_eval.py:379`'s `smoke_metric` has always gated
on `pred_trace.get("reason")`, a key that never existed, so that class of metric is only now
possible.

The reproduction that opened this section ends up fully self-describing:

```
planner reason           : no_path
outputs.status           : error
outputs.final            : None
trajectory.finish_reason : no_path
```

**Why not read the reason from the planner events instead** — it is already in
`events.planner_events[*].extra.reason`. But planner events are loaded behind a
`hasattr(state_store, "list_planner_events")` guard, so they are absent for trajectory-only stores,
including the production-trace import path this investigation started from. Only a trajectory field
works when `source_priority == "trajectory"`.

**Deliberate simplification.** `docs/proposals/RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md:309-315` makes
`outputs.status` a closed enum — `ok`, `error`, `cancelled`, `paused`, `unknown` — so there is no
`incomplete` to reach for, and every non-answer reason collapses to `error`. That flattens a real
distinction: a `no_path` finish is sometimes a legitimate clarification request
(`requires_followup`), not a failure. `finish_reason` preserves the difference for any metric that
cares; it simply is not visible in `status` alone.

**Also fixed — `_infer_status` ignored `history` whenever a trajectory existed.** It returned early
after the step-error scan, so flow-level `node_error` / `node_failed` / `node_timeout` events were
never consulted for a trace that had a trajectory. Pre-dated all of this work but was the same class
of bug: status not reflecting a real failure signal.

Flow events and trajectory steps are independent failure signals, so the failure check was extracted
into `_history_has_failure` and is now evaluated **first**: any recorded failure → `error`, otherwise
the trajectory-derived status, otherwise the history-derived one.

Worth recording how this one hid: an existing test
(`test_export_prefers_trajectory_then_planner_events_and_writes_manifest`) saved a
`StoredEvent(kind="node_failed")` and then asserted `status == "ok"`. The wrong behaviour was written
down as the expected behaviour, so the suite defended it. The assertion was corrected as part of the
fix, and a second test covers a different failure kind (`node_timeout`) so the helper is exercised on
more than one value.

**Carried forward:** the answer now lands in every persisted trajectory. That is the point, but it
means free-text model output — the likeliest place for sensitive content — reaches the state store
by default, and no redaction exists on this path (`redaction_profile` is recorded as metadata only,
never applied). Worth a deliberate retention/redaction decision by whoever owns that policy.

**Also unblocks D2:** with `outputs.final` populated on the common path, `run_import_eval.py` can
retire `_trajectory_answer` — once the agent moves off its `3.6.2` pin (see D7).

### Scoped proposals — not implemented

| # | Gap | Evidence | Open decision |
|---|---|---|---|
| B3 | Metric `checks` dict is discarded | `_as_score_payload` (`runner.py:24-43`) reads only `score` and `feedback`; every other key is dropped, and `results.jsonl` has no `checks` field | `@metric` lets authors declare `MetricCriterion` ids, but nothing persists per-criterion outcomes — which is *why* the script's `_parse_checks` (`:385`) reverse-engineers `k=0\|k=1` strings out of `feedback`. Persisting `checks` is an additive results-row schema change; needs a decision on that schema. |
| B4 | No `default_split` on export | `_infer_split_from_tags` (`export.py:22-26`) returns `"unknown"` absent a `split:` tag; `_split_dataset_payload_rows` (`api.py:1312-1324`) keeps only `val`/`test` and otherwise raises `ValueError("dataset must contain at least one val example")` | Production traces carry no `split:` tag, so an imported bundle is unusable via `evaluate_dataset` without intervention; the script's `--split` fallback (`:338-340`) patches it row by row. Needs a decision on where the default applies — export or evaluate. |

### Reclassified as conveniences — not gaps

| # | Item | Why it is not a gap |
|---|---|---|
| B5 | No "score a stored trace without re-running" `run_one` | The library contract already permits this: `run_one` is caller-supplied, and `_make_run_one` (`:357`) is ~6 lines. A shipped `make_stored_trace_run_one()` would be ergonomic sugar, not a capability unlock. |
| B6 | `collect_trace_rows` not in `penguiflow.evals.__all__` | It is public in `penguiflow/evals/export.py:326` and importable today. Re-exporting is discoverability polish. |

### Not a gap — reuse what already exists

**`_PrefetchedStore` (`:202`) duplicates `InMemoryStateStore`**
(`penguiflow/state/in_memory.py:218`, "Complete in-memory StateStore for development and
testing"). That class already implements everything `collect_trace_rows` calls —
`save_trajectory` (`:487`), `get_trajectory` (`:498`), `load_history` (`:284`),
`list_planner_events` (`:546`) — and it is already the store used throughout `tests/evals/*`
and as the default in `api.py:1064` and `api.py:1554`. Populating it via `save_trajectory`
yields an equivalent row.

The shim's docstring claims that omitting `list_planner_events` is deliberate, "so the
hasattr guard skips it". That reasoning does not hold: `InMemoryStateStore.list_planner_events`
returns `[]` for an unknown trace anyway, and `_build_trace_example` (`export.py:104`) emits
`None` for an empty planner-event list either way. The omission buys nothing.

**CSV output (`_write_csv`, `:401`)** is genuinely new — nothing in `penguiflow/` imports
`csv`. But it is a presentation concern and reasonably stays in the script.

### Bucket C — platform-backend gaps

Not penguiflow's problem. Recorded here for the PB owners.

- **No global or agent-scoped trajectory listing.** `session_id[eq]` is declared
  `Query(...)` — required — at `src/router/state_store.py:1114-1118`, and
  `state_store_trajectories` (`src/model/state_store.py:208-224`) has **no `agent_id` or
  `user_id` column**, so even adding a parameter would not help. A real listing endpoint
  would have to join `session_id → chat_sessions(user_id, agent_id)`. This is the direct
  cause of the script's N+1 session fan-out, and of `TraceSelector` being unusable.
- **No cursor pagination on `/trajectories`.** Ordering is `updated_at DESC` with a `limit`
  that has `ge=1` but **no upper bound** — so you cannot walk past `limit` rows for a session.
- **`/state-store/planner-events` has no `limit` at all**, returning an unbounded array.
- **`data` is a plain `JSON` column, not JSONB**, so no server-side querying inside a
  trajectory is possible without a migration.
- **Security finding — state plainly:** every `/state-store/*` endpoint is unauthenticated
  and unscoped, and `/v2/users/{user_id}/…` never cross-checks the path `user_id` against the
  proxy-supplied identity. Only three places apply `get_databricks_user_email`:
  `src/router/session.py:150`, `src/router/session.py:565`, and `src/util/databricks.py:93`
  (`get_current_user_profile`, used by the user router). It is explicitly **commented out** on
  `create_session` (`src/router/session.py:91`).

  This is *why* the script's `--user-id`/`--users-file` cross-user download works. The
  script's docstring (`:62-64`) tells the operator that "what you can actually read is
  whatever your credential is authorized for" — that implies a credential-scoped
  authorization which **does not exist** at the application layer. Any caller who reaches the
  app can read any trace. Correcting that wording is a prerequisite for circulating the
  script, and the underlying gap should go to the PB owners regardless of what happens to
  this script.

---

## 3. Defects and inaccuracies found in the script

Recorded, not fixed.

**D1 — `smoke_metric` can never award 1.0 on the trajectories path.**
It gates on `pred_trace.get("reason") == "answer_complete"` (`:379-381`). At the time of writing
`Trajectory.serialise()` carried no reason at all — it was a field of `PlannerFinish`
(`penguiflow/planner/models.py:394-395`), the run *result*, reaching PB only inside the `finish`
planner-event's `extra`. So every trajectory-sourced trace scored at most 0.5 and the default metric
silently understated every import.

**Updated — the reason is now on the trajectory, under a different name.** `serialise()` gained
`final_answer` (B7) and then `finish_reason` (the status follow-up), so its key set is now `query`,
`llm_context`, `tool_context`, `artifacts`, `sources`, `metadata`, `steps`, `summary`, `hint_state`,
`resume_user_input`, `steering_inputs`, `background_results`, `final_answer`, `finish_reason`
(`penguiflow/planner/trajectory.py`). `smoke_metric` still fails, but for a narrower reason: the key
is `finish_reason`, not `reason`. The one-word fix is now available where before the data did not
exist at all — apply it when the script is next touched (see D7 on the version pin).

**D2 — `_trajectory_answer` (`:236`) is effectively dead code.**
It probes `final_answer`, `answer`, `final_response`, `response` — none of which `serialise()`
emits — then falls back to `steps[-1]["answer"]`/`["final_answer"]`, which `TrajectoryStep.dump()`
(`trajectory.py:160-173`) never emits either. It returns `""` for well-formed trajectories.
The answer actually lives at `steps[-1].action.args.answer` (or legacy `raw_answer`). Note
this is the same underlying problem as **B1**: the script wrote a workaround for a library
gap, and the workaround does not work.

**Superseded by the B1 fix.** `outputs.final` is now populated upstream by
`_infer_final` (`penguiflow/evals/export.py`), so `_trajectory_answer` and the
`trace_row["outputs"]["final"] = entry.answer` patch (`:331`) become unnecessary — the library
returns the answer directly. The script cannot drop them yet: per **D7**, the agent pins
`penguiflow==3.6.2` while the fix lands on `3.9.0`, so the workaround must stay until the agent
moves up. Remove it in the same change that bumps the pin.

**D3 — planner events are never fetched, though they are the richest signal PB holds.**
`GET /state-store/planner-events?trace_id[eq]=…` carries `latency_ms`, `token_estimate`,
`error`, and cost plus the completion `reason` inside `extra`. The shim's `load_history`
returns `[]` by design (`:220-221`), so `events.flow_events` is always empty and
`events.planner_events` is always `None`. The volume asymmetry in PB's local dev database is
telling: 1187 planner-event rows against 5 trajectory rows. Fetching them would fix D1
properly and populate the `events` block the schema already defines.

**D4 — the docstring's invocation path is wrong.** It says
`uv run python scripts/run_import_eval.py` (`:47`, `:51`, `:58`, `:65`); the file is at the
repository root.

**D5 — the docstring asserts an auth mechanism that is not established.** It states PB is
"deployed with Databricks 'User Authorization' (OBO)" and that the OBO proxy accepts the
token (`:28-33`). But no deploy manifest under `deploy/` declares `user_api_scopes` or any
scopes block, and PB never reads `Authorization` or `x-forwarded-access-token` anywhere.
Whether a PAT is rejected is a property of the Databricks Apps front door, not of this
application. The observed behaviour (PAT rejected, U2M token accepted, cookie accepted) may
well be accurate; the stated *cause* is not supported. Reword to describe what was observed
without asserting a mechanism.

**D6 — `--source messages` is unverified.** *(Open item.)*
PB writes assistant `metadata` as a pure passthrough of client-supplied JSON
(`src/router/session.py:586`), and the current agent sends only `{tenant_id, user_id}`
(`creative-delivery-copilot/src/agentiv/websocket_entry.py:522`). `grep -rn "next_node"` over
the entire PB repository returns **zero hits**, and all assistant messages in the local dev
database have `metadata_json = {}`. The plausible origin of trajectory-shaped message
metadata is the legacy `AgentivStateStore`
(`creative-delivery-copilot/src/agentiv/state_store.py:128-152`), which serialised state-store
events into chat messages — a module still present and not deleted.

This path was tested in theory but never confirmed against a real environment on our side, so
it is recorded as **an open question, not as dead code**. Roughly 60 lines of the script
(`entries_from_messages`, the `messages` branch of `--source`, and the `auto` fallback) depend
on the answer.

**D7 — version skew.** This repository is penguiflow `3.9.0` (`pyproject.toml:7`); the agent
pins `penguiflow[planner,cli]==3.6.2` (`creative-delivery-copilot/pyproject.toml:12`). Any
Bucket-B fix needs a release plus an agent bump before it affects production imports. This
does not block fixing them, but it does mean the script cannot rely on them soon.

**D8 — `PB_ENVS` (`:98-103`) hardcodes four live internal Databricks Apps hostnames**
(dev, rc1, prod, staging). The file's own comment acknowledges this should be scrubbed before
publication. The file is untracked, so **this has not entered git history** — which is the
cheap moment to fix it.

---

## 4. Decisions recorded and open items

**Decided, not yet executed:** the script should move to `scripts/run_import_eval.py` —
matching its own docstring and resolving D4 — with `PB_ENVS` values replaced by
`<placeholder>` so the existing `_resolve_pb_url` guard (`:487`) fires and forces `--pb-url`
or `PB_URL_<ENV>` at runtime. Both changes are prerequisites for any commit.

**Resolved since this document was first written:**

- **B1 and B2 are implemented**, along with **B7** (the answer was never persisted) and the
  `outputs.status` follow-up (status ignored both the finish reason and flow-history failures).
  See the route map in section 2 for what each fix was and how it was verified.
- **B2's scope was corrected.** An earlier revision of this document claimed B2 "affects the
  supported CLI path". It does not — `penguiflow eval evaluate` routes through `evaluate_dataset`,
  which always awaited. Section 2 carries the corrected reachability analysis; treat that as
  authoritative over any older summary.
- **D1's fix no longer requires fetching planner events.** `finish_reason` now rides on the
  trajectory itself, so it is available even for trajectory-only stores where planner events cannot
  be fetched. Fetching them remains worthwhile only for the `events` block and per-step latency.

**Still open:**

1. Verify `--source messages` against a real environment (D6). Determines whether ~60 lines
   stay, and whether `auto` is meaningful or should collapse to trajectories-only.
2. Decide on B3 (`checks` persistence) and B4 (`default_split`) — the two remaining scoped
   proposals in section 2, each blocked on a schema decision rather than on effort.
3. Fetch planner events (D3) if the `events` block and per-step latency are wanted; it costs one
   extra HTTP round trip per trace on an already N+1-shaped import.
4. Route the Bucket-C findings — particularly the unauthenticated `/state-store/*` surface —
   to the PB owners. This is independent of whatever happens to the script.
5. Retention/redaction policy for the now-persisted answer text (see B7), and the pre-existing
   mypy/ruff violations in `penguiflow/evals/helpers.py` inherited from `f828af6`.
