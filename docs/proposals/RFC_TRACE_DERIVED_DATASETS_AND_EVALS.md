# RFC: Trace-Derived Datasets and Evals

**Status:** Draft (TODO)
**Author:** German Martin (+ Santiago Benvenuto edits)
**Created:** 2026-02-21
**Target Version:** v2.12+

---

## Summary

PenguiFlow has strong execution + debugging surfaces (Playground, trace replay, `FlowEvent` hooks), but it does not yet provide a first-class evaluation workflow:

- Turn production traces into **repeatable, versioned datasets**.
- Run **custom, reusable metrics** at orchestration and node/tool level.
- Track **quality regressions over time** (CI + local runs).
- Produce artifacts consumable by optimizers (e.g. **DSPy GEPA**) without forcing DSPy into core.
- Deploy improvements as a single, auditable configuration artifact (**PatchBundle**), not ad-hoc code edits.

This RFC proposes a capability-detected export + eval system that is:

- **Safe by default** (sensitive data does not leak from tool outputs/contexts).
- **StateStore-native** (reuses existing persistence contracts; tolerates missing capabilities).
- **Playground-first for curation** (tagging UX in Playground is the primary workflow).
- **DSPy/GEPA-compatible by design** (dataset views + metric signature).

---

## Motivation / Problem

Today, quality workflows typically look like:

1. Run the app.
2. Eyeball a Playground trace.
3. Optionally log metrics to a sink (e.g. MLflow) with custom code.

This creates two structural problems:

- **Execution-path bias**: we have great debugging primitives, but no repeatable evaluation harness.
- **No stable interface**: traces are rich but not easily converted into portable datasets + metric runs.

Optimizers like DSPy GEPA need two things:

- Datasets that can be mapped into signature-shaped examples (`dspy.Example(...)`).
- An eval harness + metric contract that is stable across manual iteration and optimizer loops.

PenguiFlow should make “quality” a first-class, trace-derived workflow.

---

## Goals

### Phase 1 (MVP): Curation + Export + Analyze-only Evals

- Define a stable, versioned trace record schema: **`TraceExampleV1`** (JSONL).
- **Playground-first tagging UX** for dataset curation (`dataset:*`, `split:*`, etc.).
- Export datasets from a configured `StateStore` using capability detection:
  - Prefer `SupportsTrajectories.get_trajectory(...)`.
  - Else fall back to `SupportsPlannerEvents.list_planner_events(...)`.
  - Else fall back to `StateStore.load_history(...)`.
- Safe-by-default export:
  - Exclude raw `tool_context`/`llm_context` by default.
  - Never inline heavy tool outputs; spill to artifacts if possible.
  - Deterministic truncation + hashing/redaction options.
- Provide a minimal “analyze-only” metrics runner that computes deterministic aggregates from exports:
  - success rate
  - p50/p95 latency by node/tool
  - tool failure rate
  - cost summaries when available

### Phase 2: Harness Evals + Patch Sweeps

- Define signature-shaped dataset projections (**`DatasetViewV1`**) + manifest provenance.
- Define patch points + a deployable, auditable config artifact (**`PatchBundleV1`**).
- Provide an eval runner that:
  - Executes PenguiFlow runs for each example (`gold`) to produce `pred` + `pred_trace`.
  - Calls a GEPA-compatible metric signature.
  - Writes per-example results JSONL + aggregate reports.
- Provide “manual sweeps”: evaluate multiple patch candidates against one dataset.

### Phase 3: Optimization Templates (Optional Dependencies)

- Provide repo-agnostic templates that:
  - Load `view.jsonl` into DSPy examples.
  - Run GEPA-style optimization against explicit patch points.
  - Output the best `PatchBundleV1` + eval reports.

---

## Non-Goals

- Building a full observability platform (OpenTelemetry exporters, Prometheus collectors, vendor SDK integrations).
- Defining a universal notion of “quality” (teams bring task-specific metrics).
- Automatic labeling / ground-truth generation for all tasks.
- Optimizing arbitrary code paths (optimization knobs must be explicit, declarative, and version-controlled).

---

## Prior Art / References (Informative)

- LangSmith: datasets and “create datasets from production traces” workflows.
- OpenAI Evals patterns: dataset + config + results as portable artifacts.
- promptfoo: config-driven evals + assertions + structured result artifacts.
- Arize Phoenix / OpenInference: trace + eval-friendly span formats.

This RFC borrows the *shape* of these systems while keeping PenguiFlow’s core principles:

- protocol-based extensibility
- capability detection
- minimal core dependencies
- artifacts for heavy payloads

---

## Terminology

- **Trace**: a single run identifier, `trace_id`.
- **Session**: a UI/user-scoped grouping identifier, `session_id`.
- **StoredEvent**: persisted runtime events derived from `FlowEvent` (core audit log).
- **PlannerEvent**: structured planner/tool execution events (Playground streaming channel).
- **Trajectory**: planner-native structure (query, steps, summary, metadata, contexts).

Artifacts proposed by this RFC:

- **Trace record**: safe-by-default representation of what happened in one run (**debugging + slicing**).
- **Dataset view**: signature-shaped projection of trace records into rows (**eval + optimization**).
- **Patch point**: explicit, named tuning surface (text/config) that can be varied between evals.
- **Patch bundle**: a concrete set of patch point values in a deployable JSON artifact.

---

## Existing PenguiFlow Building Blocks

These already exist and strongly support the RFC:

- Runtime events:
  - `FlowEvent` model (latency, attempts, queue depth, cancellation).
  - `StoredEvent.from_flow_event(...)` and `StateStore.save_event/load_history`.
- Optional `StateStore` capabilities:
  - `SupportsTrajectories.save_trajectory/get_trajectory/list_traces`.
  - `SupportsPlannerEvents.save_planner_event/list_planner_events`.
  - `SupportsArtifacts.artifact_store` + `ArtifactStore` protocol.
- Planner-native structure:
  - `Trajectory` (query, contexts, steps, observations, streams, background results, metadata).
  - Planner finish metadata already includes cost + constraints snapshots (when present).
- CLI patterns:
  - `penguiflow-admin history|replay` prints trace history JSONL.
- Patch points already exist as planner init inputs:
  - `system_prompt_extra`
  - `planning_hints`

---

## High-Level Design

### A. Trace Record vs Dataset View vs Patch Bundle

Keep three artifacts separate:

1. **TraceExampleV1** (trace record): rich, safe-by-default representation of a run.
2. **DatasetViewV1** (view rows): flat, signature-shaped dict rows for eval/optimization.
3. **PatchBundleV1** (deployable candidate): explicit configuration/text changes, no code execution.

This separation:

- preserves debugging richness without polluting eval datasets,
- makes “DSPy compatibility” a property of a view, not a property of the trace schema,
- makes optimization outputs auditable and deployable.

### B. Two Evaluation Modes

1. **Analyze-only** (no reruns): compute deterministic metrics from exported trace records.
2. **Harness eval** (reruns): run the system end-to-end for each dataset row, then score.

Both modes share:

- dataset manifests
- metric contracts
- comparable result formats

---

## Data Model (Normative)

All schemas MUST be explicitly versioned (`schema_version`) and MUST be forward-compatible via additive fields.

### 1) `TraceExampleV1` (Trace Record Row)

Format: JSON (one object per line in `trace.jsonl`).

Design constraints:

- MUST tolerate missing capabilities: exporter may produce partial rows.
- MUST remain “row-small”: large payloads MUST be truncated or externalized.
- MUST be safe-by-default: sensitive contexts and tool outputs excluded unless explicitly requested.

#### Canonical Shape (conceptual)

```json
{
  "schema_version": "TraceExampleV1",
  "trace_id": "trace_abc",
  "session_id": "session_xyz",

  "query": "User query (optional if unknown or redacted)",

  "inputs": {
    "llm_context": null,
    "tool_context": null,
    "llm_context_ref": null,
    "tool_context_ref": null
  },

  "outputs": {
    "final": null,
    "status": "ok",
    "error": null
  },

  "trajectory": {
    "summary": null,
    "metadata": {},
    "steps": [],
    "background_results": {}
  },

  "events": {
    "flow_events": null,
    "planner_events": null
  },

  "artifacts": {
    "included_refs": [],
    "omitted_reason": "safe_default"
  },

  "derived": {
    "step_count": 0,
    "node_latency_ms": {},
    "tool_failures": 0,
    "cost": null
  },

  "redaction": {
    "profile": "internal_safe",
    "rules_version": "v1",
    "fields_included": [],
    "fields_omitted": []
  },

  "provenance": {
    "exported_at": "2026-02-21T00:00:00Z",
    "exporter": "penguiflow.exporter",
    "penguiflow_version": "2.11.7",
    "state_store": {
      "capabilities_detected": ["SupportsTrajectories", "SupportsPlannerEvents"],
      "source_priority": "trajectory"
    }
  }
}
```

#### Required fields

- `schema_version`
- `trace_id`
- `outputs.status` (even if `unknown`)
- `redaction.profile`
- `provenance`

#### Status values

`outputs.status` MUST be one of:

- `ok`
- `error`
- `cancelled`
- `paused` (if trace ended with a pause)
- `unknown`

#### Size / truncation requirements

Exporter MUST implement deterministic size controls, e.g.:

- max bytes per field (stringified JSON)
- max list lengths for events/steps
- max depth / keys per mapping
- stable truncation behavior (same input → same output)

Large payloads MUST be handled via:

- `ArtifactStore` references if available
- otherwise deterministic truncation + hashing

### 2) `DatasetViewV1` (View Config + Rows)

DSPy datasets are signature-shaped and typically flat. `DatasetViewV1` provides a declarative projection layer.

#### View config (stored in `manifest.json`)

```json
{
  "schema_version": "DatasetViewV1",
  "view_name": "question_answer_v1",
  "signature": "question -> answer",
  "input_keys": ["question"],
  "field_map": {
    "query": "question",
    "outputs.final": "answer"
  },
  "row_filter": {
    "status_in": ["ok"],
    "max_bytes": 20000
  }
}
```

#### View row (stored in `view.jsonl`)

Each row MUST include:

- flat signature-shaped fields (e.g. `question`, `answer`)
- a minimal provenance block linking back to the trace

```json
{
  "question": "…",
  "answer": "…",
  "__pf": {
    "trace_id": "trace_abc",
    "session_id": "session_xyz",
    "schema_version": "DatasetViewRowV1"
  }
}
```

#### Trace path language (for `field_map`)

MVP supports dotted-path lookup into TraceExample:

- `query`
- `outputs.final`
- `trajectory.summary`
- `trajectory.metadata.tags`

Future: computed paths (hashes, joins, tool call extraction). Computation MUST remain declarative (no arbitrary code execution).

### 3) `PatchBundleV1` (Deployable Patch Artifact)

Patch bundles MUST be:

- JSON-only (no code execution)
- explicit about compatibility expectations (planner type, tool catalog hash)
- safe to store/review/deploy

```json
{
  "schema_version": "PatchBundleV1",
  "patches": {
    "planner.system_prompt_extra": "…",
    "planner.planning_hints": {"max_steps": 8},
    "tool.search.desc": "…"
  },
  "compat": {
    "planner": "ReactPlanner",
    "tool_catalog_hash": "sha256:…",
    "flow_id": "optional"
  },
  "provenance": {
    "created_at": "2026-02-21T00:00:00Z",
    "optimizer": "manual_sweep",
    "dataset": {"tags": ["dataset:foo", "split:val"]},
    "metric": "my_metric_v2",
    "score": 0.83
  }
}
```

### 4) `PatchPointRegistryV1` (Optional but Recommended)

To prevent “stringly-typed patch keys”, we SHOULD publish a machine-readable registry:

- supported patch keys
- expected types (string, mapping)
- application location (planner init, catalog build, prompt render)

This registry enables:

- validation of patch bundles
- UI affordances (“this field expects string”)
- compatibility checks

---

## Curation: Tagging UX (Playground-First)

### Why Playground-first

Dataset curation is human-centric:

- you inspect traces while debugging,
- you decide which traces are representative,
- you assign dataset + split tags at the same time.

CLI tagging exists, but it should be an additive workflow, not the primary path.

### Tag conventions (recommended)

- `dataset:<name>` (e.g., `dataset:customer_support_v1`)
- `split:train|val|test`
- optional slicing tags:
  - `route:<name>`
  - `tenant:<id>`
  - `path:<graph_hash>`
  - `tool:<tool_name>` (auto-derived)

### Storage location (MVP)

Tags live in `Trajectory.metadata["tags"]` (list[str]).

Rationale:

- zero new storage primitives needed
- round-trips today via `Trajectory.serialise()`
- works with existing `SupportsTrajectories`

### Playground UX (MVP)

In the trace inspector view:

- Display current tags as removable chips.
- Provide an “Add tag…” input with auto-suggest:
  - recently used tags in this session
  - common prefixes (`dataset:`, `split:`)
- Provide one-click buttons:
  - “Add to dataset…” (prompts for dataset name, adds `dataset:<name>`)
  - “Set split” (radio `train/val/test`)
- Provide trace list filters by tag.

### Playground API (MVP)

Add server endpoints that mutate tags for a trace:

- `GET /trajectory/{trace_id}?session_id=...` (exists)
- `PATCH /trajectory/{trace_id}/metadata?session_id=...` (new)
  - payload: `{ "tags": ["dataset:foo", "split:val", ...] }`
  - implementation: read trajectory, update metadata.tags, save trajectory

Capability constraints:

- Tag editing requires `SupportsTrajectories` (read + write).
- For stores without trajectories, UI MUST disable tagging and explain why.

### Future: indexed tagging

For large stores, scanning `list_traces(session_id, limit=N)` and filtering in memory will not scale.

Introduce optional capabilities:

- `SupportsTraceIndex.list_traces_by_tag(tag, *, limit, time_range, session_id=None)`
- or narrower `SupportsTraceTags` capability

MVP does not require this, but schemas should anticipate it (manifest provenance should record selection criteria).

---

## Export Pipeline

### Export selection modes

The exporter SHOULD support:

- by tag (primary): `--tag dataset:foo --tag split:val`
- by session_id (+limit) then filter by tags (MVP fallback)
- by explicit list of trace_ids (secondary)
- by time range (future optional capability)

### Capability detection + source priority

Exporter builds each `TraceExampleV1` row by choosing the best available sources:

1. **Trajectory path**: `SupportsTrajectories.get_trajectory(trace_id, session_id)`
2. **Planner events**: `SupportsPlannerEvents.list_planner_events(trace_id)`
3. **Audit log**: `StateStore.load_history(trace_id)` returning `StoredEvent`s

Important constraint:

- `get_trajectory()` requires `session_id`. Therefore, `--trace-id` exports without session context MUST either:
  - require `--session-id`, or
  - produce a partial row that omits trajectory-derived fields.

### Canonical “final output” extraction rules (TODO but required for v1)

We need explicit rules because traces can be:

- planner traces (have trajectory steps)
- flow-only traces (have only runtime events)
- paused/resumed traces (may be split across multiple trace_ids)

Proposed v1 extraction priority (per trace):

1. If trajectory exists:
   - if the last step action is `final_response` and has `args.answer`/`args.raw_answer`, use that as `outputs.final`.
   - else if trajectory metadata contains a known final payload field (TBD), use it.
2. Else if tasks capability exists (future exporter enhancement):
   - locate task with `trace_id` and use task `result` as `outputs.final`.
3. Else:
   - `outputs.final = null`, `outputs.status = unknown` (or `error` if errors detected)

Paused/resumed:

- v1 treats each resumed execution as a separate trace record, linked by a new optional `links.parent_trace_id` if available.
- future: add an explicit “run chain id” persisted in trajectory metadata.

### Safe-by-default export profiles

Exporter MUST support explicit export profiles; defaults MUST be safe.

#### `internal_safe` (default)

- include: trace_id, session_id, query, outputs.final (if available), derived aggregates, tags
- exclude: raw `llm_context`, raw `tool_context`
- exclude: step observations (tool outputs)
- include: step skeleton (tool name, error flags, latency, hashes)

#### `debug`

- include: limited step observations (truncated), planner events (bounded)
- still exclude: `tool_context` unless explicitly requested

#### `shareable`

- remove or hash: query, outputs.final, any string fields likely containing sensitive content
- include only aggregates + structural metadata

#### `replayable` (future)

- include fixtures sufficient to replay tool calls safely (pure/read tools only), preferably as artifact refs

### Redaction interface (MVP)

The exporter SHOULD support:

- a built-in allowlist/denylist of fields
- deterministic truncation + hashing
- optional user hook (Python callable) for domain-specific redaction

The hook interface must be non-invasive:

```python
def redact_trace_example(row: dict) -> dict:
    ...
```

No code execution from data files; hooks are explicit code imports.

### Artifacts

Exporter should use `ArtifactStore` when available:

- large observations → `ArtifactRef`
- trace record keeps only references + small previews/digests

If no artifact store exists, exporter MUST:

- truncate deterministically
- record omissions in `redaction.fields_omitted`

---

## Dataset Views (Projection)

### Why views are separate

Trace records are multi-level and rich; eval datasets need flat signature-shaped rows.

Views enable:

- one trace record format, many tasks/signatures
- stable optimization loops without changing trace schema

### View authoring UX (Playground-first, incremental)

MVP:

- a simple “Export as view…” modal:
  - choose a built-in view template (e.g., `query -> answer`)
  - optionally map `outputs.final` to `answer`
  - preview first N rows

Future:

- a view builder UI:
  - pick signature (or import path in template repos)
  - map trace paths to fields
  - validate rows and show warnings (missing fields, too large, status != ok)

---

## Metrics and Evals

### Metric API (GEPA-compatible)

PenguiFlow adopts the GEPA metric contract as its default:

```python
def metric(
    gold: object,
    pred: object,
    trace: object | None = None,
    pred_name: str | None = None,
    pred_trace: object | None = None,
) -> float | dict:
    # returns:
    # - float score
    # - or {"score": float, "feedback": str}
    ...
```

Notes:

- `gold` is typically the dataset row (view row or trace record), dict-like.
- `pred` is the model/program output for that example.
- `trace` enables deterministic metrics from execution data.
- `pred_name`/`pred_trace` enable component-level feedback for optimizers.

### Analyze-only runner (Phase 1)

Consumes `trace.jsonl` and emits:

- `metrics.jsonl` per trace
- `report.json` aggregates
- optional `summary.csv`

Metrics examples:

- success rate from `outputs.status`
- p50/p95 latency per node from `FlowEvent.latency_ms` when present
- tool failure rate from `node_error`/`node_timeout` events
- cost when available from planner metadata snapshots

### Harness eval runner (Phase 2)

Consumes `view.jsonl` (gold rows) and executes:

- `pred = run_one(gold, patch_bundle)`
- optional `pred_trace` (a trace record from the evaluation run)
- `metric(gold, pred, trace=gold_trace?, pred_trace=pred_trace)`

Outputs:

- `predictions.jsonl` (pred + provenance)
- `results.jsonl` (metric score + optional feedback)
- `report.json` aggregates + slices

---

## Patch Points, Patch Application, and Reproducibility

### Patch points (initial committed set)

Phase 2 supports these patch points:

- `planner.system_prompt_extra` (string)
- `planner.planning_hints` (mapping)
- `tool.<tool_name>.desc` (string)

Future candidates (explicitly deferred):

- `tool.<tool_name>.examples`
- tool group directory overlays
- validation/repair tuning knobs
- route policies

### Patch application rules

Patch application MUST be:

- deterministic
- validated against the patch point registry (if present)
- logged into eval manifests for reproduction

Application locations:

- planner patch points applied at planner init / prompt build time
- tool description patches applied during catalog build or tool record render

### Compatibility hashing

To prevent “apply patch bundle to incompatible system”:

- compute a `tool_catalog_hash` from tool records (names + desc + schemas)
- include it in patch bundle compat
- runner warns or fails if mismatch (configurable)

---

## Tool Replay Mode (Future, High Leverage)

For cost + determinism + privacy, add an optional VCR-like replay mode:

- Export a fixture set of tool calls and outputs (pure/read tools only).
- Eval harness can run in `STUB` mode:
  - tool call matches fixture fingerprint → returns recorded output
  - otherwise fails closed

This requires:

- stable tool call fingerprinting:
  - tool name
  - normalized args (sorted keys) or args hash
- storing outputs as artifact refs (to avoid inlining sensitive content)
- respecting tool side effects (`NodeSpec.side_effects`) to prevent replaying write/external tools by accident

---

## Optional Integrations

### MLflow (sink, not core dependency)

Core artifacts are canonical:

- dataset JSONL
- view JSONL
- patch bundles
- results JSONL
- reports

Optional sinks (like MLflow) can log:

- manifests as artifacts
- aggregate scores as metrics
- tags / candidate ids as params

### OpenInference / OTel-friendly exports (future)

Provide an optional adapter that maps trace records into OpenInference-ish span structures for teams using Phoenix/OTel stacks.

This remains “templates/sinks”, not core.

---

## Security / Privacy

Primary risk: sensitive data leakage via exports.

Mitigations (MUST):

- safe-by-default export profiles that exclude contexts and tool outputs
- deterministic truncation + hashing options
- explicit opt-in for sensitive fields
- artifact references instead of inline payloads
- redaction hook interface for downstream needs

Operational guidance (SHOULD):

- avoid exporting raw tool outputs from production systems
- treat datasets as sensitive artifacts and store in controlled locations
- add CI guards to prevent accidental commit of dataset JSONL files

---

## Proposed Module / File Layout (Implementation Sketch)

This RFC does not mandate exact paths, but a coherent layout helps:

```
penguiflow/evals/
  schema.py            # TraceExampleV1, DatasetViewV1, PatchBundleV1 models
  export.py            # StateStore capability detection + JSONL exporter
  redact.py            # export profiles + truncation + hashing utilities
  views.py             # projection engine + manifest writer
  analyze.py           # analyze-only metrics runner
  runner.py            # harness eval runner
  patches.py           # patch point registry + patch application
```

Playground additions:

- API endpoints to mutate trajectory tags/metadata
- UI components to edit tags + trigger export

CLI additions (optional early, not primary):

- `penguiflow eval export …`
- `penguiflow eval analyze …`
- `penguiflow eval run …`

---

## Phased Roadmap (TODO)

### Phase 1A: Playground Tagging + Trace Export

Deliverables:

- Tag editor in Playground (trajectory metadata tags)
- Export `trace.jsonl` + `manifest.json`
- Analyze-only metrics runner
- Docs + example workflow

Success criteria:

- A team can curate a dataset in Playground and export it safely.
- A team can compute deterministic regression metrics locally and in CI.

### Phase 1B: Dataset Views + Manifest

- `DatasetViewV1` config + `view.jsonl` export
- view templates + preview
- provenance improvements

### Phase 2: Harness Evals + Patch Sweeps

- `PatchBundleV1`
- patch application engine
- harness runner + result formats
- manual sweeps with candidate provenance

### Phase 3: DSPy/GEPA Templates

- optional dependency templates under `penguiflow[planner]` or templates-only package
- GEPA loop producing PatchBundle candidates

### Phase 4: Replay + Indexing + Interop

- replay fixtures and stub tool execution
- indexed tag lookup capability
- OpenInference exports as optional adapter

---

## Open Questions (TODO)

- Where should commands live?
  - keep `penguiflow-admin` for low-level history/replay, add `penguiflow eval` for eval workflows?
- Canonical extraction of “final output”:
  - planner traces (trajectory-based) vs flow-only traces (events-only) vs tasks results
- Pause/resume semantics:
  - treat as separate traces or a linked run chain?
- Redaction policy interface:
  - config file vs Python hook vs both?
- Initial patch points:
  - commit to the smallest useful set and publish in docs + registry

---

## TODO Checklist (First Iteration)

### Schemas / Contracts

- [ ] Specify `TraceExampleV1` as an explicit JSON schema (optionality + size limits).
- [ ] Specify `DatasetViewV1` + view row provenance (`__pf` block).
- [ ] Specify `PatchBundleV1` + compat hashing.
- [ ] Specify eval result + report schemas.

### Playground (Primary UX)

- [ ] Add tag editing UI (chips + add/remove + split selector).
- [ ] Add backend endpoint to update trajectory metadata tags.
- [ ] Add dataset export action (modal + profile selection + view template selection).

### Exporter

- [ ] Implement capability-detected exporter:
  - [ ] from `get_trajectory()`
  - [ ] from `list_planner_events()`
  - [ ] from `load_history()`
- [ ] Implement safe export profiles + deterministic truncation.
- [ ] Add export-by-tag behavior (scan session traces for MVP).
- [ ] Write `manifest.json` with provenance and selection criteria.

### Evals

- [ ] Analyze-only metrics runner + report formats.
- [ ] Define harness runner API + GEPA-compatible metric signature.
- [ ] Manual sweep runner (list of PatchBundle candidates).

### Templates / Integrations

- [ ] DSPy dataset loader for `view.jsonl` into `dspy.Example(...).with_inputs(...)`.
- [ ] GEPA template producing PatchBundle candidates.
- [ ] Optional MLflow sink logging artifacts + metrics.

---

## Appendix A: Example Files

### A1) Dataset export layout

```
dataset/
  manifest.json
  trace.jsonl
  view.jsonl
  results.jsonl        # (if harness eval)
  report.json
  patches/
    best.patchbundle.json
    candidates.jsonl
```

### A2) Example metric

```python
def exact_match_metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    gold_answer = gold.get("answer")
    if not isinstance(gold_answer, str):
        return 0.0
    return 1.0 if str(pred).strip() == gold_answer.strip() else 0.0
```

