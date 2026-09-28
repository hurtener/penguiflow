# Refactor Plan: Dual Local and MLflow Evaluation Backends

- **Status:** Proposed
- **Date:** 2026-08-27
- **Scope:** PenguiFlow evaluation execution and persistence
- **Related:**
  [Framework-Agnostic Learning Control Plane](./FRAMEWORK_AGNOSTIC_LEARNING_CONTROL_PLANE.md)

## 1. Decision

Refactor PenguiFlow evaluation around shared local execution and metric semantics with
two optional evaluation backends:

- **Local backend:** preserves current JSONL, offline/CI, StateStore, and local
  report behavior without requiring MLflow.
- **MLflow backend:** uses MLflow traces, Evaluation Datasets,
  `mlflow.genai.evaluate()`, scorers, evaluation runs, and assessments as the
  evidence store for standalone MLflow-backed evaluations.

Do not place the current local evaluation loop in front of MLflow. Do not make
MLflow understand PenguiFlow trajectories directly. Both backends use the same
local `run_one` semantics and metric logic through thin adapters.

The minimal shared surface is:

```python
class RunOne(Protocol):
    async def __call__(self, inputs: EvaluationInputs) -> PredictionResult: ...


class Metric(Protocol):
    async def score(
        self,
        case: EvaluationCase,
        prediction: PredictionResult,
    ) -> ScoreResult: ...


class EvaluationBackend(Protocol):
    async def evaluate(
        self,
        dataset: object,
        run_one: RunOne,
        metrics: Sequence[Metric],
    ) -> EvaluationResult: ...
```

Do not introduce separate dataset-provider, tracker, run-store, or orchestrator
interfaces yet. Split those concerns only when a concrete mixed-backend use
case requires it.

## 2. Why This Path

### 2.1 Why not wrap the current API around MLflow

Wrapping the current API wholesale would retain two dataset models, two
evaluation engines, two result formats, and ambiguous ownership. MLflow would
become a secondary blob and metrics sink rather than the source of truth for
MLflow-backed learning jobs.

The current package already contains overlapping execution paths in
`penguiflow/evals/api.py`, `runner.py`, `sweep.py`, and `workflow.py`. New MLflow
support should not add another translation layer around all of them.

### 2.2 Why not replace all current evaluation code

Current evaluation code contains PenguiFlow-specific behavior that MLflow does
not provide:

- agent and project discovery;
- isolated StateStore injection;
- trace persistence waiting and verification;
- full trajectory retrieval;
- LLM/tool context separation;
- planner-aware trajectory helpers;
- candidate and patch integration points;
- offline and no-service execution.

These capabilities remain in PenguiFlow's local evaluation callable and shared
metrics; this proposal does not introduce remote evaluation execution.

### 2.3 Why not build Learning Plane integration here

Standalone MLflow evaluation is useful without continuous learning. A separate
`PenguiFlowLearningPlaneProvider` may reuse this backend, but trajectory
publication, cohort curation, candidate-use proof, approval, and delivery are
outside this refactor.

## 3. Confirmed Feasibility Findings

The enterprise v2 feasibility work confirmed:

1. A normal PenguiFlow inference can create an MLflow root trace with nested
   LiteLLM spans.
2. A trace can be projected into an MLflow Evaluation Dataset record.
3. Nested PenguiFlow evidence survives MLflow dataset write and readback.
4. Native short-term-memory context is available in
   `PlannerFinish.metadata["llm_context"]` after hydration, even though the root
   span input contains only pre-hydration context.
5. A middle-turn conversation snapshot can be stored and used by a fresh
   inference to recover required prior facts.
6. `mlflow.genai.evaluate()` can consume a trace-derived MLflow dataset, invoke
   an agent through `predict_fn`, execute a custom scorer, persist an evaluation
   run, and link a new inference trace.

The successful native feasibility run produced:

- Dataset ID: `d-1e4a724ea9e14794824ce38cdf662a2d`
- Evaluation run ID: `3a2b3fa8aded4a3eacb3df18f09606c2`
- `required_answer_facts/mean`: `1.0`
- Evaluation-linked inference traces: `1`

The feasibility scripts are:

- `examples/planner_enterprise_agent_v2/evals/mlflow_trace_dataset_feasibility.py`
- `examples/planner_enterprise_agent_v2/evals/mlflow_learning_plane_feasibility.py`

## 4. What Is Not Yet Proven

The successful run proves API-path feasibility, not production completeness.
It does not yet prove:

- prediction-side StateStore trajectory persistence in the MLflow path;
- full prediction trajectory delivery to an MLflow scorer;
- adaptation of an existing PenguiFlow trajectory metric;
- safe async execution without per-case `asyncio.run()`;
- failure, pause, cancellation, and timeout representation;
- immutable dataset revisions or content-addressed evaluation snapshots;
- enforced redaction of contexts and tool observations;
- complete source-record-run-prediction-scorer lineage;
- reproducible candidate runtime configuration;
- operation with another agent framework.

These are parity and hardening gates, not blockers to the selected architecture.

## 5. Ownership Boundaries

| Concern | Owner |
|---|---|
| Agent construction and execution | Local PenguiFlow evaluation callable |
| StateStore isolation and trajectory persistence | Local PenguiFlow evaluation callable |
| Native trajectory interpretation | PenguiFlow metric/helper code |
| Domain success criteria | Agent evaluation package |
| Local JSONL and local reports | `LocalEvaluationBackend` |
| MLflow datasets, runs, traces, assessments | `MLflowEvaluationBackend` |
| Evaluation trace-to-case projection and redaction | Agent evaluation package |
| Investigation trajectory and learning lifecycle | Separate Learning Plane provider |
| Runtime conversation memory | Agent runtime StateStore, not MLflow |

MLflow is the evaluation-evidence store for MLflow-backed jobs. This backend does
not replace runtime StateStore or require a Learning Control Plane.

## 6. Shared Data Contracts

Keep contracts small and JSON-safe. Avoid standardizing every native trajectory
field.

### 6.1 Evaluation inputs

```python
@dataclass
class EvaluationInputs:
    query: str
    llm_context: Mapping[str, Any]
    tool_context: Mapping[str, Any]
```

Only replay-safe context belongs here. Runtime callbacks, loggers, telemetry
objects, trace IDs, status publishers, and mutable execution progress do not.

### 6.2 Evaluation case

```python
@dataclass
class EvaluationCase:
    case_id: str
    inputs: EvaluationInputs
    expectations: Mapping[str, Any]
    tags: Mapping[str, str]
    source: Mapping[str, Any]
```

`expectations` stays agent-owned. It may include required answer facts,
reference outputs, labels, or the minimal reference-trajectory evidence needed
by configured metrics.

### 6.3 Prediction result

```python
@dataclass
class PredictionResult:
    status: Literal["ok", "paused", "failed", "cancelled"]
    answer: str | None
    route: str | None
    trajectory: Mapping[str, Any] | None
    effective_llm_context: Mapping[str, Any]
    effective_tool_context: Mapping[str, Any]
    trace_ids: Mapping[str, str]
    error: Mapping[str, Any] | None
```

An incomplete or missing trajectory must be explicit. It must not silently
become an empty successful prediction.

### 6.4 Score result

```python
@dataclass
class ScoreResult:
    score: float
    feedback: str | None = None
    checks: Mapping[str, Any] = field(default_factory=dict)
```

Preserve structured checks. Do not encode them only inside feedback strings.

### 6.5 Evaluation result

```python
@dataclass
class EvaluationResult:
    backend: Literal["local", "mlflow"]
    metrics: Mapping[str, float]
    case_results: Sequence[Mapping[str, Any]]
    backend_refs: Mapping[str, str]
```

For MLflow, `backend_refs` includes dataset, evaluation run, and trace IDs. For
local execution it includes report and artifact paths when requested.

## 7. Local PenguiFlow Evaluation Callable

Both backends call the same locally executed `run_one`. It may use PenguiFlow's
discovered planner/orchestrator path or an existing project-supplied callable.
Remote orchestration protocols are outside this library refactor.

Responsibilities:

1. Build or obtain the local planner/orchestrator.
2. Inject an isolated StateStore when supported.
3. Pass replay-safe inputs and controlled context.
4. Execute one local agent run and collect its trajectory.
5. Return a complete `PredictionResult` and execution references.
6. Preserve pause, failure, cancellation, and timeout status.

Candidate skill comparison is a separate local configuration concern. A
candidate arm may make the exact skill available through PenguiFlow's existing
`skills_provider` machinery or a small in-memory overlay; the backend does not
interpret or persist the skill. The Learning Plane provider separately binds
the candidate digest to use proof.

Both backends use the same local callable semantics. This is the consistency
boundary: the same inputs and local execution profile must produce comparable
results regardless of evidence backend.

## 8. Shared Metrics and Backend Adapters

Metric logic remains ordinary project/Penguiflow code. Backend adapters only
translate callable and result shapes.

```text
Shared metric implementation
├── Local metric adapter
│   └── called by LocalEvaluationBackend
└── MLflow scorer adapter
    └── exposed through @mlflow.genai.scorers.scorer
```

First adapted metric should be an existing trajectory-aware enterprise policy
metric, not another answer-substring scorer. The adapter must supply:

- dataset expectations/reference evidence;
- prediction answer and route;
- serialized prediction trajectory;
- prediction status and error;
- source and prediction trace references.

Async metrics must be supported without calling `asyncio.run()` inside an
active event loop. Backend execution should own event-loop boundaries.

## 9. Local Evaluation Backend

`LocalEvaluationBackend` is the compatibility and offline implementation.

It preserves:

- current JSONL inputs;
- local/CI execution without MLflow;
- existing local agent discovery and execution behavior;
- StateStore-backed trajectory capture;
- local artifacts and reports;
- existing CLI/API entry points where practical.

The current public API becomes a facade selecting this backend by default.
Existing callers should not need to opt into a new class immediately.

Do not expand the local backend during this refactor. Consolidate overlapping
evaluation loops after local execution and metric parity exists.

## 10. MLflow Evaluation Backend

`MLflowEvaluationBackend` uses MLflow-native primitives:

```python
mlflow.genai.evaluate(
    data=dataset,
    predict_fn=predict_adapter,
    scorers=scorer_adapters,
)
```

Responsibilities:

1. Accept an MLflow Evaluation Dataset or dataset ID.
2. Convert record inputs into `EvaluationInputs`.
3. Invoke local `run_one`; `predict_fn` remains the thin MLflow callable boundary.
4. Return JSON-safe prediction evidence to MLflow.
5. Wrap shared metrics as MLflow scorers.
6. Persist evaluation run IDs, per-case assessments, and prediction traces.
7. Return normalized `EvaluationResult` with MLflow references.

The backend does not publish `InvestigationTrajectoryV1`, select learning
cohorts, prove candidate use, manage proposals, or deliver learned assets. Those
are responsibilities of the separate Learning Plane provider.

MLflow record shape:

```text
inputs
  query
  llm_context
  tool_context

expectations
  agent-owned labels/reference evidence

tags
  split/cohort/case type/schema and metric versions

source
  source MLflow trace ID
  native framework trace ID
```

Avoid embedding complete native trajectories when scorers only need a small
projection. Retain an immutable trace/artifact reference for deeper inspection.

## 11. Evaluation-Case Projection and Replay Context

The source trace has two relevant context views:

- root input context before framework hydration;
- effective context after memory and runtime context preparation.

For replay, use the effective context captured in
`PlannerFinish.metadata["llm_context"]`, then remove runtime-derived fields such
as `status_history`.

This snapshot is passed as ordinary inference context during evaluation. Do not
write private STM internals into StateStore, rerun prior conversation turns, or
parse rendered LLM prompts.

Projection must use an allowlist or project-defined schema. A fixed denylist of
known callback fields is insufficient for security and reproducibility.

## 12. Refactor Phases

### Phase 0: Preserve feasibility evidence

- Keep current feasibility scripts as throwaway executable references.
- Record known dataset/run IDs in this decision document.
- Stop extending the current trace-to-JSONL-to-MLflow round-trip path.

Exit criterion: current native MLflow feasibility remains reproducible.

### Phase 1: Consolidate local evaluation execution

- Reuse current local discovery, StateStore handling, persistence waiting, and
  trajectory retrieval behind `run_one`.
- Return explicit statuses and complete prediction evidence.
- Make both current local API and MLflow `predict_fn` use the same local callable.

Exit criterion: local evaluation behavior remains stable and MLflow prediction
returns a persisted trajectory.

### Phase 2: Normalize metric logic

- Define `ScoreResult` and preserve structured checks.
- Adapt one existing enterprise trajectory metric to the shared contract.
- Add local callable and MLflow scorer wrappers.

Exit criterion: same case and prediction produce equivalent score/checks through
both wrappers.

### Phase 3: Add MLflow backend

- Accept MLflow Dataset object or ID.
- Call `mlflow.genai.evaluate()` with local `predict_fn` and metric adapters.
- Persist and return source trace, dataset, evaluation run, prediction trace,
  metric version, model, and config references.

Exit criterion: no JSONL or current local evaluation loop participates in the
MLflow run.

### Phase 4: Make current API the local facade

- Route existing API/CLI calls to `LocalEvaluationBackend`.
- Preserve signatures where inexpensive.
- Deprecate duplicate internal evaluation loops rather than immediately deleting
  compatibility paths.

Exit criterion: local backend works without MLflow installed or reachable.

### Phase 5: Evidence hardening

- Enforce projection/redaction policy.
- Pin dataset revision/content digest.
- Capture explicit failures and exclusions for every frozen case.
- Add model, deployment, metric, scorer, and config fingerprints.

Exit criterion: standalone evaluation evidence is reproducible and exposes
stable references that an optional Learning Plane provider can consume.

## 13. Initial File Plan

Exact names may change during implementation, but keep responsibilities narrow:

```text
penguiflow/evals/
  models.py              # EvaluationInputs/Case/Prediction/Score/Result
  local_run.py           # Local run_one construction and evidence capture
  metrics.py             # Shared metric normalization/adaptation
  backends/
    local.py             # Current local loop adapter
    mlflow.py             # Optional MLflow-native backend
```

MLflow imports must remain inside optional adapter code. Core planner and local
evaluation must not require MLflow.

Agent evaluation packages continue to own project-specific trace projection and
scorers:

```text
examples/planner_enterprise_agent_v2/evals/
  projector.py
  metrics.py
  mlflow_scorers.py
```

Learning Plane trajectory publication belongs in its provider package, not in
either evaluation backend.

## 14. Compatibility Strategy

- Keep existing local API behavior as default.
- Add backend selection without changing existing required arguments initially.
- Return existing summary fields plus optional normalized backend references.
- Keep JSONL import/export as local/offline compatibility, not enterprise source
  of truth.
- Keep current metric decorator working through a compatibility adapter.
- Avoid moving MLflow record shapes into generic planner modules.

No immediate deletion is required. Remove obsolete duplicate evaluation loops
only after local and MLflow parity checks pass.

## 15. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| MLflow lock-in | Keep local execution, metrics, and local backend free of MLflow types |
| Context or tool-output leakage | Enforced allowlist projection before dataset publication |
| Dataset mutation | Pin revision/content digest in evaluation evidence |
| Missing prediction trajectory | Fail prediction explicitly after persistence wait/readback |
| Async event-loop conflicts | Backend owns async boundary; no per-case nested `asyncio.run()` |
| Metric divergence | One shared implementation with local and MLflow wrappers |
| Trace identity confusion | Distinguish source, native prediction, and MLflow prediction IDs |
| Candidate is not actually applied | Local skill overlay plus Learning Plane candidate-use proof |
| Invalid holdout evidence | Independent source IDs and non-overlapping cohort manifests |
| Over-generalization | Delay extra provider interfaces until another backend requires them |

## 16. Acceptance Criteria

Refactor is complete when:

1. Existing local evaluation runs through `LocalEvaluationBackend` without
   requiring MLflow.
2. Both backends use the same local `run_one` semantics.
3. MLflow evaluation consumes an MLflow Dataset directly.
4. MLflow prediction returns persisted PenguiFlow trajectory evidence.
5. One existing trajectory-aware metric runs through both local and MLflow
   adapters with equivalent results.
6. Middle-turn replay preserves required conversation context without hidden
   StateStore manipulation.
7. Failed, paused, cancelled, and missing-trajectory cases are explicit.
8. Every MLflow case links source trace, dataset record/revision, evaluation run,
   prediction trace, scorer version, and model/config fingerprint.
9. Local JSONL and MLflow datasets are backend-native persistence formats; no
   mandatory round-trip through both exists.
10. MLflow evaluation works without Learning Plane configuration and returns
    stable references usable by an optional Learning Plane provider.

## 17. Non-Goals

- Replacing runtime StateStore with MLflow.
- Making MLflow responsible for candidate promotion or activation.
- Publishing `InvestigationTrajectoryV1` or its query index.
- Learning cohort curation, candidate-use proof, approval, or skill delivery.
- Standardizing all agent-framework trajectories.
- Migrating every existing metric before one trajectory-aware metric proves the
  adapter contract.
- Building a universal dataset-provider/tracker abstraction before a concrete
  third backend exists.
- Removing local JSONL/CI support.
- Treating the current one-case feasibility result as production validation.

## 18. Final Architecture

```mermaid
graph LR
    C[Evaluation cases] --> L[LocalEvaluationBackend]
    D[MLflow Evaluation Dataset] --> M[MLflowEvaluationBackend]

    L --> P[Local run_one]
    M --> P

    P --> A[Local agent + isolated StateStore]
    A --> R[PredictionResult]

    R -->|Local run| LM[Local metric adapter]
    R -->|MLflow run| MS[MLflow scorer adapter]

    LM --> LR[Local reports/artifacts]
    MS --> MR[MLflow runs/traces/assessments]
```

Consistency lives in local execution and metric semantics. Persistence and
evaluation tracking remain backend-native.
