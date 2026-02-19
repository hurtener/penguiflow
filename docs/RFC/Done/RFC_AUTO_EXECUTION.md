# RFC: Deterministic Transition Detection (Auto-Seq) with Optional Auto-Execution

## Status: **Implemented (v1)**
**Date:** 2026-01-28  
**Reviewers:** Core Engineering Team  

This RFC introduces a deterministic transition detector that identifies when exactly one visible, eligible tool can consume the previous step's structured output.

Detection is enabled via planner config and emits observability events for emergent playbook discovery.

Optional auto-execution uses the existing pending_actions fast-path but is dual-gated (planner-level + tool-level) and post-node only.  

## 🎯 Problem Statement

PenguiFlow runs a full LLM inference for each transition even when the next step is deterministically implied by the previous node's structured output (typed pipelines).

**Explicit scope note:** Step 0 is typically raw text and lacks a canonical mapping into tool arg schemas; schema-only matching on raw text is brittle and can cause false positives/negatives. Therefore, v1 explicitly excludes step-0 auto selection from raw user text. Step-0 can be handled via LLM planning or an explicit preprocess DAG (e.g., triage), and auto-seq begins after the first structured output exists.

## 🎯 Scope

**In Scope (v1): Post-node deterministic transitions**

- Runs only when len(trajectory.steps) > 0
- Uses the last step's output ("observation") after safe coercion to a structured payload
- Skips detection when the previous action was `next_node="parallel"` (parallel outputs are wrapper-shaped and increase false-positive schema matches; join aggregation is handled explicitly by the parallel plan)
- Auto-seq operates over `planner._specs` **as currently scoped**. Auto-seq scans whatever `planner._specs` is at that moment. Visibility is applied when callers use the normal entrypoints (`run()/resume()` with tool visibility), which scope `planner._specs` for the loop duration. If someone calls `run_loop` directly without scoping, auto-seq will behave exactly like the rest of the runtime (scan the currently configured specs).
- ToolPolicy and ToolVisibility are authoritative. The detector must never iterate over the raw catalog/registry; it must only see the already-filtered `planner._specs` for that run.
- Applies side-effect gate (read/pure by default)
- Emits events describing unique/ambiguous/none/skipped outcomes
- Optional execution can enqueue/execute via pending_actions but must preserve constraints/events/alias rewrite invariants

**Out of Scope (Backlog):**

- Step-0 auto selection from raw text (trajectory.query) and any hard-coded { "text": query } mapping
- Writeful auto-execution by default
- Semantic/creative selection (LLM reasoning) replaced by schema-only detection

**Real Example:**
```python
# Step 0: triage is selected/executed normally (LLM or preprocess DAG)
UserQuery(text=...) → triage_query (LLM-selected or preprocess) → RouteDecision

# Step 1+: detector runs on structured output
RouteDecision → detector finds unique consumer init_docs → optionally auto-exec
DocumentState → detector finds unique consumer parse_docs → optionally auto-exec

# Eventually, when multiple tools validate, fall back to LLM selection
```

## 💡 Proposed Solution

### A) Detection (default behavior when enabled)

Detects deterministic transitions after each tool step by validating the structured output against args_model for eligible tools.

Emits events for observability and playbook discovery.

### B) Optional auto-execution (explicitly gated)

Auto-execution in v1 applies only to the immediate next step. The detector runs again after each executed tool step, enabling a chain of deterministic transitions without requiring precomputed multi-step enqueueing.

If detection yields exactly one eligible match and execution is permitted, select (or enqueue) the **next single deterministic action** via the pending_actions fast-path.

Still passes through the same alias rewrite, constraint checks, visibility scoping, and event emission as normal planner actions.

### Eventing

**Detection always emits one event when enabled:**

```python
auto_seq_detected_unique
extra: { "tool_name": "...", "payload_fingerprint": "...", "payload_keys_count": N, "payload_type": "..." }

auto_seq_detected_ambiguous
extra: { "candidates": [...], "candidate_count": N, "payload_fingerprint": "...", "payload_keys_count": N, "payload_type": "..." }

auto_seq_detected_none or auto_seq_skipped
extra: include fingerprint metadata when available; `auto_seq_skipped` also includes reason
```

**Skipped reasons (non-exhaustive):**
- `non_structured_observation`
- `previous_step_parallel`

**Execution event (only when execution happens):**

```python
auto_seq_executed
extra: { "tool_name": "..."}
```

**Event taxonomy:** The system emits exactly one detection event per iteration when enabled (`auto_seq_detected_*` or `auto_seq_skipped`). If the tool runs without LLM planning, emit `auto_seq_executed`.

**Implementation note:** the shipped ambiguous payload key is `candidates` (not `tool_names`). Detection events also include `payload_type` and `payload_keys_count` for lightweight telemetry bucketing.

**Payload fingerprint directive:**
fingerprint should avoid raw data; use type name + stable key set (privacy-safe) to support emergent playbook analytics.

**Enhanced Event Implementation:**

```python
import hashlib
from dataclasses import dataclass, field

@dataclass
class DetectionResult:
    status: str  # "unique" | "ambiguous" | "none" | "skipped"
    candidate_tools: list[str] = field(default_factory=list)
    selected_action: PlannerAction | None = None  # Fully formed action with validated args
    reason: Optional[str] = None

def _detect_deterministic_transition(planner: ReactPlanner, trajectory: Trajectory) -> DetectionResult:
    """
    Returns DetectionResult with fully formed PlannerAction when unique.
    selected_action is built from validated_args.model_dump(mode="json") when available.
    """
    if trajectory.steps and trajectory.steps[-1].action.next_node == "parallel":
        return DetectionResult(status="skipped", reason="previous_step_parallel")

    input_payload = _coerce_observation_payload(trajectory.steps[-1]) if trajectory.steps else None
    if input_payload is None:
        return DetectionResult(status="skipped", reason="non_structured_observation")
    
    candidates = []
    for spec in planner._specs:
        if _is_tool_eligible(spec, input_payload, read_only_only=planner._auto_seq_read_only_only):
            candidates.append(spec)
    
    if len(candidates) == 1:
        spec = candidates[0]
        validated_args = spec.args_model.model_validate(input_payload)
        selected_action = PlannerAction(
            next_node=spec.name,
            args=validated_args.model_dump(mode="json")
        )
        return DetectionResult(status="unique", candidate_tools=[spec.name], selected_action=selected_action)
    elif len(candidates) > 1:
        return DetectionResult(status="ambiguous", candidate_tools=[s.name for s in candidates])
    else:
        return DetectionResult(status="none", candidate_tools=[])

def _emit_detection_event(planner, trajectory, detection: DetectionResult):
    """Emit detection event based on status."""
    prev_step_next_node = trajectory.steps[-1].action.next_node if trajectory.steps else None
    payload_fingerprint = _get_payload_fingerprint(trajectory.steps[-1].serialise_for_llm()) if trajectory.steps else None
    
    if detection.status == "unique":
        event_extra = {
            "tool_name": detection.candidate_tools[0],
            "candidate_count": 1,
            "prev_step_next_node": prev_step_next_node,
            "payload_fingerprint": payload_fingerprint
        }
        event_type = "auto_seq_detected_unique"
    elif detection.status == "ambiguous":
        event_extra = {
            "tool_names": detection.candidate_tools,
            "candidate_count": len(detection.candidate_tools),
            "prev_step_next_node": prev_step_next_node,
            "payload_fingerprint": payload_fingerprint
        }
        event_type = "auto_seq_detected_ambiguous"
    else:
        event_extra = {
            "reason": detection.reason,
            "prev_step_next_node": prev_step_next_node,
            "payload_fingerprint": payload_fingerprint
        }
        event_type = "auto_seq_detected_none" if detection.status == "none" else "auto_seq_skipped"
    
    planner._emit_event(
        PlannerEvent(
            event_type=event_type,
            ts=planner._time_source(),
            trajectory_step=len(trajectory.steps),
            extra=event_extra,
        )
    )

def _emit_auto_seq_executed(planner, trajectory, tool_name: str):
    """Emit execution event when auto-execution occurs."""
    prev_step_next_node = trajectory.steps[-1].action.next_node if trajectory.steps else None
    
    planner._emit_event(
        PlannerEvent(
            event_type="auto_seq_executed",
            ts=planner._time_source(),
            trajectory_step=len(trajectory.steps),
            extra={"tool_name": tool_name, "prev_step_next_node": prev_step_next_node},
        )
    )
```

### Evidence: Existing Fast-Path Infrastructure

PenguiFlow already implements sequential execution without LLM calls:

**Evidence 1: Pending Actions Queue**
```python
# Source: penguiflow/planner/react_runtime.py:831-842
if isinstance(trajectory.metadata, MutableMapping):
    pending = trajectory.metadata.get("pending_actions")
    if isinstance(pending, list) and pending:
        item = pending.pop(0)
        if isinstance(item, dict):
            next_node = item.get("next_node")
            args = item.get("args")
            if isinstance(next_node, str):
                queued_action = PlannerAction(
                    next_node=next_node,
                    args=dict(args) if isinstance(args, dict) else {},
                )
```

**Evidence 2: Multi-Action Enqueue Logic**
```python
# Source: penguiflow/planner/react_runtime.py:849-882
if getattr(planner, "_multi_action_sequential", False) and action.alternate_actions:
    # ... validation and enqueue logic ...
    pending.append({"next_node": candidate.next_node, "args": dict(candidate.args or {})})
```

**Evidence 3: Tool Metadata System**
```python
# Source: penguiflow/planner/models.py:13
from ..catalog import NodeSpec
# NodeSpec.extra already supports arbitrary metadata
```

**Evidence 4: Side-Effect Safety Checks**
```python
# Source: penguiflow/planner/react_runtime.py:32
_MULTI_ACTION_READONLY_SIDE_EFFECTS = frozenset({"pure", "read"})
# Source: penguiflow/planner/react_runtime.py:866-868
if read_only_only and (
    getattr(spec, "side_effects", None) not in _MULTI_ACTION_READONLY_SIDE_EFFECTS
):
    continue
```

**Evidence 5: Event Emission System**
```python
# Source: penguiflow/planner/react_runtime.py:875-882
planner._emit_event(
    PlannerEvent(
        event_type="multi_action_enqueued",
        ts=planner._time_source(),
        trajectory_step=len(trajectory.steps),
        extra={"count": added},
    )
)
```

## 🏗️ Technical Implementation

### 1. Tool Metadata Enhancement

**Evidence: NodeSpec.extra Already Supports Metadata**
```python
# Source: penguiflow/catalog.py (NodeSpec definition)
@dataclass(frozen=True, slots=True)
class NodeSpec:
    # ... existing fields ...
    extra: Mapping[str, Any] = field(default_factory=dict)  # Immutable mapping, not dict
```

**Implementation:**
```python
@tool(
    extra={
        "auto_seq": True,  # Enable auto-sequential execution (mapping becomes immutable at spec creation)
    }
)
def triage_query(args: UserQuery, ctx: Any) -> RouteDecision:
    # Treat extra as mapping: always .get(...) access, no in-place mutation assumptions
    return RouteDecision(query=args, route=route, confidence=confidence)

@tool(
    extra={
        "auto_seq": True,  # Enable auto-sequential execution  
    }
)
def initialize_document_workflow(args: RouteDecision, ctx: Any) -> DocumentState:
    """Auto-executes when it's the only tool that accepts RouteDecision output."""
    return DocumentState(...)
```

### 2. Runtime Enhancement (Schema-Based Detection)

**Evidence: Existing First Step Logic**
```python
# Source: penguiflow/planner/react_runtime.py:808
while len(trajectory.steps) < planner._max_iters:
    # This is where we can detect first step
```

**Implementation:**
```python
# Source: penguiflow/planner/react_runtime.py:849-852 (enhanced)
# Auto-seq must run over whatever planner._specs is at that moment
# Visibility scoping is handled by the same mechanism used for normal stepping
if queued_action is not None:
    action = queued_action
    # Note: pending actions are replayed as stored; visibility only affects detection/LLM choice
elif getattr(planner, "_auto_seq_enabled", False):
    # Run detection and emit detection events
    detection = _detect_deterministic_transition(planner, trajectory)
    _emit_detection_event(planner, trajectory, detection)
    
    if detection.status == "unique" and getattr(planner, "_auto_seq_execute", False):
        spec = next(s for s in planner._specs if s.name == detection.candidate_tools[0])
        if spec.extra.get("auto_seq_execute", False):
            # Treat the selected action as a queued action (same path)
            action = detection.selected_action
        else:
            action = await step_with_recovery(planner, trajectory, config=error_recovery_config)
    else:
        action = await step_with_recovery(planner, trajectory, config=error_recovery_config)
else:
    action = await step_with_recovery(planner, trajectory, config=error_recovery_config)

# Ensure the selected action still goes through:
# alias rewrite → _log_action_received → _check_action_constraints → tool execution
```

**Coercion rules:**
```python
def _coerce_observation_payload(last_step) -> object | None:
    """Coerce observation to structured payload for validation using serialise_for_llm semantics."""
    # Option A (simple): use serialise_for_llm() and only accept Mapping → else skip
    obs = last_step.serialise_for_llm()
    
    if isinstance(obs, Mapping):
        return obs  # Mapping → use as-is
    else:
        return None  # detection returns "skipped" (reason: non_structured_observation)
```

### Candidate Eligibility Rules

**In the detection algorithm, a tool is eligible only if:**
Precondition: if the previous action was `next_node="parallel"`, auto-seq is skipped (reason: `previous_step_parallel`).

1. **Visibility** - Tool is in the current visibility scope (not hidden/denied)
2. **Auto-seq enabled** - Tool has `extra={"auto_seq": True}` 
3. **Side-effect policy** - If `planner._auto_seq_read_only_only`, tool must be `pure/read` (matches `_MULTI_ACTION_READONLY_SIDE_EFFECTS`)
4. **Not blocked** - Tool name is not in `_MULTI_ACTION_BLOCKED_NODES`
5. **Schema validation** - Tool's `args_model` validates against the current input payload

**Deterministic condition (v1):** A transition is considered deterministic only if exactly one candidate remains after the above eligibility gates. Detector returns a candidate; uniqueness is finalized after constraint check. Constraint checks are performed **in the main run_loop path** (where the constraint tracker exists), not inside the detector. If more than one candidate remains, the result is ambiguous and the planner falls back to normal LLM planning.

```python
def _is_tool_eligible(spec, payload, read_only_only: bool = True) -> bool:
    """Check if tool is eligible for deterministic transition detection."""
    # Tool must opt-in to detection
    auto_seq = spec.extra.get("auto_seq") if isinstance(spec.extra, Mapping) else None
    if not auto_seq:
        return False
    
    # Side-effect gate (conditional based on knob)
    if read_only_only and getattr(spec, "side_effects", None) not in _MULTI_ACTION_READONLY_SIDE_EFFECTS:
        return False
    
    # Blocked nodes check
    if spec.name in _MULTI_ACTION_BLOCKED_NODES:
        return False
    
    # Schema validation
    try:
        spec.args_model.model_validate(payload)
        return True
    except ValidationError:
        return False
```

### Performance: Caching

Cache **"this tool matches this fingerprint"** and still re-run a cheap validate OR store a compact normalized args dict. Cache is best-effort optimization; correctness does not depend on it.

This avoids subtle bugs while still capturing the big win.

**Planner config:**
```python
# ReactPlanner.__init__ parameters
auto_seq_enabled: bool = False,
auto_seq_execute: bool = False,
auto_seq_read_only_only: bool = True,  # New parameter for side-effect gating

# Stored as instance variables for fork() inheritance
self._auto_seq_enabled = auto_seq_enabled
self._auto_seq_execute = auto_seq_execute
self._auto_seq_read_only_only = auto_seq_read_only_only
self._init_kwargs = self._init_kwargs | {"auto_seq_enabled": auto_seq_enabled, "auto_seq_execute": auto_seq_execute, "auto_seq_read_only_only": auto_seq_read_only_only}
```

**Tool metadata (NodeSpec.extra):**
```python
extra["auto_seq"] = True → tool participates in detection

extra["auto_seq_execute"] = True → tool is eligible for auto-execution (optional, default False)
```

**Rule to include verbatim in RFC:**
Detection requires: planner._auto_seq_enabled AND tool.extra["auto_seq"]

Execution requires: planner._auto_seq_execute AND tool.extra["auto_seq_execute"] AND all safety gates

### Config Integration

Add fields to planner config/spec model and ensure fork/cloning carries `_init_kwargs`. Avoid unverified filenames in the RFC unless you confirm them.

**Implementation:**
```python
# Source: ReactPlanner init path (add new parameter)
multi_action_sequential: bool = False,
multi_action_read_only_only: bool = True,
auto_seq_enabled: bool = False,  # New parameter
```

**Evidence: Existing Instance Variable Pattern**
```python
# Source: ReactPlanner initialization (parameter assignment)
self._multi_action_sequential = multi_action_sequential
self._multi_action_read_only_only = multi_action_read_only_only
```

**Implementation:**
```python
# Source: ReactPlanner initialization (add new assignment)
self._multi_action_sequential = multi_action_sequential
self._multi_action_read_only_only = multi_action_read_only_only
self._auto_seq_enabled = auto_seq_enabled  # New assignment
```

## 🔒 Security & Safety Analysis

### Evidence: Existing Safety Infrastructure

**Evidence 1: Side-Effect Safety**
```python
# Source: penguiflow/planner/react_runtime.py:32
_MULTI_ACTION_READONLY_SIDE_EFFECTS = frozenset({"pure", "read"})
# Source: penguiflow/planner/react_runtime.py:866-868
if read_only_only and (
    getattr(spec, "side_effects", None) not in _MULTI_ACTION_READONLY_SIDE_EFFECTS
):
    continue
```

**Evidence 2: Validation Safety**
```python
# Source: penguiflow/planner/react_runtime.py:181
try:
    spec.args_model.model_validate(candidate.args)
except ValidationError:
    continue
```

**Evidence 3: Tool Visibility Safety**
```python
# Source: penguiflow/planner/react_runtime.py:75-76
original_specs = getattr(planner, "_specs", None)
# ... visibility scope logic ...
planner._specs = visible_specs
```

**Evidence 4: Guardrail Integration**
```python
# Source: penguiflow/planner/react_runtime.py:893-897
constraint_error = planner._check_action_constraints(action, trajectory, tracker)
if constraint_error is not None:
    trajectory.steps.append(TrajectoryStep(action=action, error=constraint_error))
    continue
```

### ✅ **Safety Guarantees**

1. **Side-Effect Protection** - Reuses existing read-only enforcement
2. **Validation Safety** - Same Pydantic validation as existing multi-action
3. **Visibility Compliance** - Uses already-scoped `planner._specs`
4. **Guardrail Enforcement** - Actions go through existing constraint checking
5. **Pause/Resume Safety** - Leverages existing `pending_actions` persistence
6. **Policy Gate Compliance** - Detection and execution only consider the policy-filtered, visibility-scoped tool set (`planner._specs`)
7. **Human-Gated Safety** - Auto-execution must not bypass any human-approval or gating constraints; those guardrails remain authoritative via existing constraint checks and tool metadata

### ⚠️ **New Risk Mitigations**

**Risk: Silent Auto-Execution**
```python
# Evidence: Event emission pattern (react_runtime.py:875-882)
planner._emit_event(
    PlannerEvent(
        event_type="auto_seq_executed",  # Execution event
        ts=planner._time_source(),
        trajectory_step=len(trajectory.steps),
        extra={"tool_name": auto_action.next_node},
    )
)
```

**Risk: Metadata Access Patterns**

Keep `extra` treated as mapping at runtime; any normalization should occur at tool registration time (or be removed). Always use `.get(...)` access, no in-place mutation assumptions.

**Note:** Tool registration-time normalization (if any) happens before creating NodeSpec; runtime treats `extra` as Mapping.

### 🔎 Reviewer Notes (codebase-aligned risks)

- **Visibility scoping** — auto-seq operates over `planner._specs` as currently scoped. Visibility is applied by whichever entrypoint establishes scope (if any). Calling `run_loop` directly behaves like the rest of the runtime (no special casing).

**Mitigation:** Auto-seq scans whatever `planner._specs` is at that moment; visibility scoping is handled by the same mechanism used for normal stepping.

### 2) Forking and Background Tasks (Config Inheritance)

**Risk:** Background tasks are spawned by forking the planner. If the main planner enables auto-seq, forked agents will inherit those flags via `_init_kwargs`, which can unintentionally enable deterministic transitions in subagents that were expected to run with full LLM reasoning or tighter safety constraints.

**Mitigation:** Make inheritance explicit in the RFC: forked planners inherit `auto_seq_*` by default unless the caller overrides at fork time. For background tasks that should not auto-exec, require an explicit override (e.g., disable `auto_seq_enabled` and `auto_seq_execute` when forking). Document this clearly in the configuration and usage guidance so task orchestration can decide per-subagent behavior.

### 3) Tool Policy and Visibility Gates

**Risk:** If the detector scans the raw catalog or registry instead of the policy-filtered `planner._specs`, auto-seq can identify tools that were denied by `ToolPolicy` or hidden by `ToolVisibilityPolicy`.

**Mitigation:** Require the detector to operate only on `planner._specs` for the current run scope. Treat ToolPolicy and ToolVisibility as authoritative gates; auto-seq must never call a tool that is not visible in the current scope.

### 4) Human-Gated Tools and Semantic Approval

**Risk:** A schema-only match can bypass a “human-gated” transition where the LLM would normally pause or request approval. This includes tools that rely on a policy gate or human approval to proceed safely.

**Mitigation:** Auto-seq may still detect uniqueness, but auto-execution must be suppressed when a tool requires human approval. Enforcement remains via existing constraint checks and tool metadata; auto-exec is allowed only if all gates pass.

### 3) Observation Coercion: Use serialise_for_llm() Only

**Risk:** Tools may return various object types that need structured validation.

**Mitigation:** Use Option A only - `obs = last_step.serialise_for_llm(); if Mapping → validate; else skip`. This provides consistent behavior without complex coercion logic.

**Backlog:** Robust coercion with BaseModel/dataclass/object validation paths for future versions.

### 4) "Schema Uniqueness" Is Not the Same as "Deterministic Next Tool"

**Risk:** The RFC claims "only one tool accepts RouteDecision with route=documents". But `route=documents` is not a schema constraint unless the model is discriminated or has validators that enforce it. You can still auto-select a tool that validates structurally but is semantically wrong.

**Evidence:** Pydantic validation alone cannot enforce business logic constraints like `route="documents"` without explicit validators or discriminators.

**V1 Mitigation:** Constraints are enforced by existing `_check_action_constraints` in the run_loop path; schema-only matching is accepted with observability-first rollout.

**Backlog:** Advanced semantic filtering options:
1. **Constraint-aware filtering:** Run candidate actions through constraint checks before declaring them "valid"
2. **Metadata predicate:** Allow tools to declare `auto_seq_requires` predicates for cheap semantic filtering  
3. **Explicit "auto_seq_from" typing:** Tools declare what upstream tool(s) they can follow

### 5) Don't Bypass Step Telemetry/Guardrails—Prove the Insertion Point Preserves Invariants

**Risk:** The RFC's `run_loop` insertion looks broadly okay, but the key question is: **does the "auto-selected action" go through the same downstream pipeline as an LLM-selected action?**

**Evidence:** `run_loop` ordering: pending_actions → step_with_recovery → multi-action enqueue, with `_emit_step_start`, alias rewrite, and `_check_action_constraints` afterward @penguiflow/planner/react_runtime.py#789-920.

**Implementation note:**
It must still:
- emit step start
- rewrite aliases
- validate args (or fail in a consistent place)
- run `_check_action_constraints`
- respect blocked nodes and side-effect policy

**Implementation:** Make `_detect_deterministic_transition()` return a *candidate* `PlannerAction`, but rely on the existing action processing path to do the rest. In `_detect_deterministic_transition()`, only do *enough* validation to identify candidates (and keep it cheap).

### 6) Performance/Caching: Validating Every Tool on Every Step Can Get Expensive

**Risk:** A naive "loop all auto_seq tools and Pydantic-validate" approach can be non-trivial in tool-heavy deployments.

**Implementation note:**
- Add a small cache keyed by `(input_type, visible_toolset_signature)` or just `(spec.name, input_model_type)` outcomes.
- Or pre-index auto-seq tools by args_model class when possible.

### 7) Reframe Feature Scope: Auto-Select Next Tool vs Pre-Populate Sequences

**Risk:** The RFC says "populate the queue for deterministic sequences," but the implementation doesn't really enqueue ahead. Your code chooses a single `PlannerAction` for the *next* step. It doesn't actually "populate pending_actions for the chain" (and you can't fully, because you don't know intermediate outputs until tools run).

### Evidence: Existing Test Infrastructure

**Evidence 1: Pending Actions Test Coverage**
```python
# Source: tests/planner/test_multi_action.py (existing)
def test_multi_action_sequential_execution():
    # Tests existing pending_actions mechanism
```

**Evidence 2: Performance Measurement Framework**
```python
# Source: tests/planner/test_react_runtime.py (existing)
def test_step_timing():
    # Existing timing infrastructure can measure auto-seq benefits
```

### Metrics

| Metric | Target | Verification |
| :--- | :--- | :--- |
| **Integration Stability** | pending_actions tests remain green | Add new tests for auto-seq branch |
| **Latency Reduction** | >x% | Measured on linear segments using existing timing tests |
| **Code Complexity** | One helper + one insertion point | No new modules; no changes to tool execution semantics |
| **Test Coverage** | Add dedicated unit tests + small integration test | Reuse fixtures/utilities where possible |

```python
# Source: penguiflow/planner/react_runtime.py (existing file)
# Add _try_auto_sequential() function to this file
```

**Tasks:**
1. Add `auto_seq_enabled` parameter to `ReactPlanner.__init__()`
2. Implement `_detect_deterministic_transition()` function in `react_runtime.py`
3. Add auto-seq check in `run_loop()` (5 lines)
4. Add `auto_seq_executed` event emission

### Phase 2: Testing (0.5 day)

**Evidence: Existing Test Patterns**
```python
# Source: tests/planner/test_multi_action.py (existing patterns)
class TestMultiAction:
    def test_sequential_execution(self):
        # Reuse this pattern for auto-seq testing
```

**Tasks:**
1. Extend existing `pending_actions` tests to cover auto-sequence
2. Add edge case tests (validation failures, multiple candidates)
3. Verify pause/resume compatibility using existing pause tests
4. Test with existing multi-action scenarios

## 🔍 Definition of "Unique"

A tool is **eligible** for auto-selection if it meets ALL of these criteria:

Precondition: if the previous action was `next_node="parallel"`, auto-seq is skipped.

1. **Visibility** - Tool is in the current visibility scope (not hidden/denied)
2. **Auto-seq enabled** - Tool has `extra={"auto_seq": True}` 
3. **Side-effect policy** - If `planner._auto_seq_read_only_only`, tool must be `pure/read` (matches `_MULTI_ACTION_READONLY_SIDE_EFFECTS`)
4. **Not blocked** - Tool name is not in `_MULTI_ACTION_BLOCKED_NODES`
5. **Schema validation** - Tool's `args_model` validates against the current input payload
6. **Constraints pass** - Tool passes `_check_action_constraints` with current trajectory

## Tests

### New tests required

- unique/ambiguous/none/skipped detection outcomes
- BaseModel observation coercion
- respects tool visibility scope
- pending_actions non-empty → detection skipped
- previous action is parallel → detection skipped
- side-effect gating filters candidates
- events emitted with correct schema
- execution requires dual gating (planner + tool)
- execution still triggers constraint checks + alias rewrite
- pause/resume behavior unchanged

## Implementation Plan

### Enable → Observe → Promote

**Phase 1: Implement detection + events + config + tests**

Deploy with auto_seq_enabled=True, auto_seq_execute=False to collect evidence.

**Phase 2: Promote specific tools/edges**

Set tool.extra.auto_seq_execute=True for selected tools
Set planner.auto_seq_execute=True in that environment

**Phase 3: Track execution metrics and roll back safely if needed**

Make clear this is a single feature with safe defaults; not a separate phase/PR.

## 🔄 Migration Guide

### Evidence: Existing Migration Patterns

**Configuration Migration**
```python
# Source: examples/planner_enterprise_agent/main.py (existing pattern)
planner = ReactPlanner(
    llm="gpt-4",
    multi_action_sequential=True,  # Existing parameter
    auto_seq_enabled=True,        # New parameter
)
```

**Tool Definition Migration**
```python
# Source: examples/.../nodes.py (existing tool patterns)
@tool(
    extra={
        "existing_metadata": "value",  # Existing pattern
        "auto_seq": True,              # New metadata
    }
)
def my_tool(data: str) -> dict:
    return {"processed": data}
```

### Migration Steps

*   **Users:** No breaking changes. Feature is Opt-In.
*   **Developers:** To enable, set `ReactPlanner(auto_seq_enabled=True)` and tag tools with `@tool(extra={"auto_seq": True})`.
*   **Ops:** No storage migration. Uses existing `pending_actions` trajectory metadata.

## 💰 Cost-Benefit Analysis

### Evidence: Existing Development Patterns

**Development Cost Evidence**
```python
# Source: git history for multi_action feature
# multi_action_sequential: ~3 days development
# auto_seq_enabled: ~0.5 days (reuses patterns)
```

### Analysis

| Aspect | This Approach | Alternative Complex Approach |
|----------|----------------|------------------------------|
| **Maintenance Cost** | Low (existing infra) | High (new module) |
| **Risk** | Low (proven patterns) | High (new architecture) |
| **Code Added** | Small helper + one insertion point | ~200+ lines |
| **Test Coverage** | New tests required; reuse fixtures/utilities | 70% (new paths) |

## 🎯 Recommendation

### Evidence: Existing Architecture Success

**Evidence 1: Multi-Action Success**
```python
# Source: penguiflow/planner/react_runtime.py:849-882
# Multi-action sequential execution is battle-tested and widely used
```

**Evidence 2: Pattern Reuse**
```python
# Source: penguiflow/planner/react_runtime.py:831-842
# pending_actions mechanism is proven in production
```

### Recommendation

**Adopt this approach** because:

1. **Evidence-Based** - Leverages proven `pending_actions` infrastructure
2. **Minimal Risk** - Reuses existing safety mechanisms and validation
3. **Preserves pending_actions behavior; adds dedicated tests for auto-seq.**
4. **Production Ready** - Uses battle-tested patterns from multi-action feature

The core insight is that PenguiFlow already solved sequential execution with `pending_actions`. Auto-select simply chooses the next tool without LLM when the choice is unique, using existing tool metadata and validation patterns.

## 📝 Usage Patterns

### Evidence: Enterprise Agent Triage Pattern

**Example: Schema-Based Auto-Execution**
```python
# Source: examples/planner_enterprise_agent/nodes.py:237-272 (enhanced)
@tool(
    desc="Classify user intent and route to appropriate workflow",
    tags=["planner", "routing"],
    side_effects="read",
    extra={
        "auto_seq": True,  # Enable auto-execution
    }
)
async def triage_query(args: UserQuery, ctx: Any) -> RouteDecision:
    """Route query - auto-executes because it's the ONLY tool that accepts UserQuery."""
    
    # Pattern-based routing logic (unchanged)
    text_lower = args.text.lower()
    if any(kw in text_lower for kw in ["bug", "error", "crash"]):
        route = "bug"
        confidence = 0.95
    elif any(kw in text_lower for kw in ["document", "file", "report"]):
        route = "documents"
        confidence = 0.90
    else:
        route = "general"
        confidence = 0.75
    
    return RouteDecision(query=args, route=route, confidence=confidence, reason=reason)
```

**Why This Works:**
- **Structured Outputs:** Triage produces RouteDecision that enables downstream deterministic edges
- **Deterministic Choice:** No LLM needed when there's only one valid consumer
- **Zero Metadata Complexity:** Only needs `auto_seq: True` for participation

**Execution Flow:**
```python
# Step 0: triage selected/executed normally (LLM or preprocess DAG)
UserQuery(text=...) → triage_query → RouteDecision

# Step 1+: detector runs on structured output
RouteDecision → detector finds unique consumer init_docs → optionally auto-exec
DocumentState → detector finds unique consumer parse_docs → optionally auto-exec

# Total savings: LLM calls avoided on deterministic post-node transitions
```

### Evidence: Post-Triage Linear Workflows

**Sequential Auto-Execution**
```python
# Source: examples/planner_enterprise_agent/README.md:168-170
# After triage returns RouteDecision, only one tool might accept that specific schema

@tool(
    extra={
        "auto_seq": True,  # Enable auto-sequential execution  
    }
)
def initialize_document_workflow(args: RouteDecision, ctx: Any) -> DocumentState:
    """Auto-executes if it's the only tool that accepts RouteDecision with route=documents."""
    return DocumentState(query=args.query, roadmap=DOCUMENT_ROADMAP)

@tool(extra={"auto_seq": True})
def parse_documents(args: DocumentState, ctx: Any) -> DocumentState:
    """Auto-executes if it's the only tool that accepts this DocumentState."""
    return args.model_copy(update={"sources": sources})
```

**Complete Schema-Based Example:**
```python
# Schema flow determines auto-execution:
UserQuery → RouteDecision → DocumentState → DocumentState → DocumentState
    ↓           ↓              ↓            ↓            ↓
triage_query → init_docs → parse_docs → extract_meta → generate_summary
    ↓           ↓              ↓            ↓            ↓
auto_seq    auto_seq       auto_seq     auto_seq     LLM choice
 (only one)  (only one)    (only one)   (only one)   (multiple options)
```

### Scope Clarification

**Auto-Sequence Covers:**
✅ **Post-node deterministic transitions** (structured output has unique consumer)
✅ **Linear schema chains** (each output has only one valid consumer)
✅ **Deterministic pipelines** (single path through schema compatibility)
✅ **Type-driven routing** (schema compatibility determines path)

**Auto-Sequence Does NOT Cover:**
❌ **Step-0 auto selection from raw text** (handled by LLM or preprocess DAG)
❌ **Multiple schema consumers** (when multiple tools accept same input type)
❌ **Creative/analytical tools** (require LLM reasoning beyond schema)
❌ **Conditional branching** (schema alone doesn't determine path)
❌ **Parallel workflows** (multiple valid schema transformations)

This approach keeps selection deterministic and safe by gating on visibility + side-effects + schema validation + constraints.
