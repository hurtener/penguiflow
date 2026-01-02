# RFC: ReactPlanner Modular Refactor

**Status:** Draft  
**Author:** Santiago Benvenuto + Claude  
**Created:** 2026-01-02  
**Target Version:** v2.10 (Backward Compatible)  

---

## Summary

This RFC proposes a modular refactor of the ReAct planner implementation by extracting focused modules out of `penguiflow/planner/react.py` (currently a single 4,442 LOC file). The goal is to:

- Reduce cognitive load (smaller, composable modules)
- Improve testability (unit-testable subsystems)
- Preserve backward compatibility (public API and behavior remain stable)

---

## Table of Contents

1. [Motivation](#motivation)
2. [Part 1: Modular Refactoring](#part-1-modular-refactoring)
   - [Current State Analysis](#current-state-analysis)
   - [Proposed Module Extractions](#proposed-module-extractions)
   - [Implementation Phases](#implementation-phases)
   - [Risk Assessment](#risk-assessment)
3. [Combined Implementation Plan](#combined-implementation-plan)
4. [Testing Strategy](#testing-strategy)
5. [Migration Guide](#migration-guide)
6. [Acceptance Criteria](#acceptance-criteria)

---

## Motivation

The `penguiflow/planner/react.py` module has become a “god file”. This is increasingly expensive to iterate on because changes to one subsystem (streaming, repair, artifacts, memory, pause/resume, reflection) require navigating and regression-testing a large intertwined surface.

This RFC narrows scope to **structure only**: no new output formats and no behavior changes beyond strictly necessary refactoring adjustments.

---

## Part 1: Modular Refactoring

### Current State Analysis

#### File Size Breakdown (Current)

These metrics are derived from AST spans (`lineno`/`end_lineno`) in `penguiflow/planner/react.py`.

```
react.py total: 4,442 lines

Classes:
├── ReactPlanner                      3,320 lines (~74.7%)
├── _EventEmittingArtifactStoreProxy    126 lines
├── _PlannerContext                     188 lines
├── _StreamingArgsExtractor             119 lines
├── _StreamingThoughtExtractor           89 lines
├── _ArtifactCollector                   33 lines
├── _SourceCollector                     30 lines
├── _StreamChunk                          9 lines
├── _ArtifactChunk                        10 lines
└── _ShortTermMemorySummary               2 lines

Module-level functions: 14 functions, 362 lines
```

#### Largest ReactPlanner Methods (By LOC)

Top offenders in `ReactPlanner` today:

| Method | LOC | Why it matters |
|--------|-----|----------------|
| `_run_loop()` | 867 | Central state machine; mixes execution + reflection + pause + events |
| `step()` | 260 | LLM call + streaming extraction + validation/repair loop |
| `__init__()` | 254 | Feature wiring; hard to reason about default behaviors |
| `_attempt_arg_fill()` | 176 | Multi-step error-path logic (high regression surface) |
| `_attempt_finish_repair()` | 141 | Repair pipeline; interacts with prompting and validation |

#### Critical Issues Identified

1. **Oversized orchestration**
   - `_run_loop()` is 867 LOC and currently couples: tool execution, observation handling, reflection, pause/resume, hint tracking, artifact/source collection, and event emission.

2. **Coupled LLM I/O + repair + streaming**
   - `step()` combines prompt wiring, streaming extraction, schema validation, salvage, repair prompting, and a fallback finish path.

3. **Duplicate streaming parsing logic**
   - `_StreamingArgsExtractor._extract_string_content()` and `_StreamingThoughtExtractor._extract_string_content()` are nearly identical (empirically ~0.89 similarity), ideal for consolidation behind a shared helper.

### Proposed Module Extractions

The goal is to extract “leaf” subsystems first (stateless, narrow dependencies), then work inwards.

#### Tier 1: High Priority

##### 1. `streaming.py` (250–350 LOC)

**Extract:**
- `_StreamChunk`, `_ArtifactChunk`
- `_StreamingArgsExtractor`, `_StreamingThoughtExtractor`
- **NEW**: `_JsonStringBufferExtractor` (shared escape-handling implementation used by both extractors)

**Why:** Isolated logic with clear unit tests already present (`tests/test_react_helpers.py` covers these today).

**Risk:** ✅ Lowest (pure parsing and dataclasses)

##### 2. `planner_context.py` (200–250 LOC)

**Extract:**
- `_PlannerContext` (ToolContext implementation)

**Why:** Already isolated; reduces noise in `react.py`.

**Risk:** ✅ Very low

##### 3. `validation_repair.py` (400–650 LOC)

**Extract:**
- Context coercion: `_validate_llm_context()`, `_coerce_tool_context()`
- PlannerAction salvage: `_salvage_action_payload()`, `_summarize_validation_error()`
- Arg-fill pipeline: `_default_for_annotation()`, `_autofill_missing_args()`, `_scan_placeholder_paths()`, `_is_arg_fill_eligible()`, `_parse_arg_fill_response()`, `_attempt_arg_fill()`
- Finish repair pipeline: `_attempt_finish_repair()`, `_parse_finish_repair_response()`
- Invalid response recording: `_record_invalid_response()`
- Arg validation: `_apply_arg_validation()`

**Why:** These are the densest error-path behaviors (and the biggest long-term maintenance cost). They are also heavily unit-testable once extracted.

**Risk:** ⚠️ Medium (complex control flow; must preserve metadata/events semantics)

#### Tier 2: Medium Priority

##### 4. `artifact_handling.py` (250–350 LOC)

**Extract:**
- `_EventEmittingArtifactStoreProxy`
- `_ArtifactCollector`, `_SourceCollector`
- Helpers: `_extract_source_payloads()`, `_source_field_map()`, `_produces_sources()`, `_model_json_schema_extra()`, `_normalise_artifact_value()`

**Why:** Clear boundaries; reduces planner loop complexity and improves test isolation for artifact/source behaviors.

**Risk:** ✅ Low

##### 5. `payload_builders.py` (250–400 LOC)

**Extract:**
- `_fallback_answer()`
- Observation guardrails: `_clamp_observation()`, `_truncate_observation_preserving_structure()`, `_emit_observation_clamped_event()`
- Final response shaping: `_build_final_payload()`, `_build_failure_payload()`

**Why:** Useful to test payload formatting and clamping in isolation, and reduces `_run_loop()` size/concern mixing.

**Risk:** ⚠️ Medium (payload shape stability matters for downstream / Playground)

##### 6. `pause_management.py` (100–200 LOC)

**Extract:**
- `pause()`, `_pause_from_context()`, `_record_pause()`
- `_store_pause_record()`, `_load_pause_record()`

**Why:** Encapsulates pause persistence and keeps orchestration clearer.

**Risk:** ✅ Low

#### Tier 3: Optional (Nice-to-have)

##### 7. `memory_integration.py` (300–450 LOC)

**Extract:**
- `_ShortTermMemorySummary`
- Memory key + isolation: `_resolve_memory_key()`, `_extract_memory_key_from_tool_context()`
- Memory context injection: `_apply_memory_context()`
- Hydration/persist hooks: `_maybe_memory_hydrate()`, `_maybe_memory_persist()`
- Turn recording: `_build_memory_turn()`, `_maybe_record_memory_turn()`

**Why:** Memory integration is relatively small in LOC, but scattered and cross-cutting; extracting it clarifies core flow and makes future memory strategies less invasive.

**Risk:** ⚠️ Medium (multiple call sites; must preserve isolation semantics)

### Post-Refactoring Architecture (Target)

```
penguiflow/planner/
├── __init__.py              # Public API exports
├── react.py                 # Orchestrator (smaller, delegates subsystems)
├── models.py                # Data schemas & protocols (unchanged)
├── context.py               # ToolContext protocol (unchanged)
├── trajectory.py            # Execution history (unchanged)
├── llm.py                   # LLM communication (unchanged)
├── prompts.py               # Prompt templates (unchanged)
├── parallel.py              # Parallel execution (unchanged)
├── memory.py                # Memory subsystem (unchanged)
├── constraints.py           # Budget/deadline (unchanged)
├── hints.py                 # Planning directives (unchanged)
├── pause.py                 # Pause signal (unchanged)
│
├── streaming.py             # NEW: Streaming chunk extraction
├── planner_context.py       # NEW: Execution context wrapper
├── validation_repair.py     # NEW: Parse/repair/salvage logic
├── artifact_handling.py     # NEW: Artifact/source collection + proxy
├── payload_builders.py      # NEW: Observation guardrails + payload shaping
├── pause_management.py      # NEW: Pause persistence + bookkeeping
└── memory_integration.py    # NEW: Memory hooks (optional tier)
```

### Implementation Phases

The sequencing favors low-risk, high-confidence moves first and defers the `_run_loop()` split until the supporting subsystems are already separated.

#### Phase 1: Foundation (Lowest Risk)

1. Extract `streaming.py`
2. Extract `planner_context.py`
3. Consolidate duplicate string extraction into `_JsonStringBufferExtractor`

#### Phase 2: Artifact + Payload Isolation (Low/Medium Risk)

4. Extract `artifact_handling.py`
5. Extract `payload_builders.py`

#### Phase 3: Validation + Repair Isolation (Medium Risk)

6. Extract `validation_repair.py`
7. Extract `pause_management.py`

#### Phase 4: Orchestration Decomposition (Highest Risk)

8. Refactor `_run_loop()` internally into 5–10 focused private methods (no module move yet), aiming to:
   - isolate “LLM action selection”
   - isolate “tool execution + observation handling”
   - isolate “finish / pause / budget exit paths”
   - isolate “event emission points”

9. Optionally extract `memory_integration.py` (Tier 3), once `react.py` responsibilities are clearer after Phase 1–3.

### Risk Assessment

| Module/Change | Complexity | Test Effort | Breaking Risk | Total Risk |
|--------------|------------|-------------|---------------|------------|
| `streaming.py` extraction | Low | Low | None | ✅ Lowest |
| `planner_context.py` extraction | Low | Low | None | ✅ Very Low |
| `artifact_handling.py` extraction | Medium | Medium | Low | ✅ Low |
| `pause_management.py` extraction | Low | Low | Low | ✅ Low |
| `payload_builders.py` extraction | Medium | Medium | Medium (payload shape) | ⚠️ Medium |
| `validation_repair.py` extraction | High | High | Medium (repair semantics) | ⚠️ Medium |
| `_run_loop()` decomposition | High | High | Medium | ⚠️⚠️ Highest |
| `memory_integration.py` extraction (optional) | Medium | Medium | Medium (isolation semantics) | ⚠️ Medium |

---

## Combined Implementation Plan

### Week 1: Foundation Extractions

| Day | Task | Effort | Risk |
|-----|------|--------|------|
| 1 | Extract `streaming.py` + shared escape helper | 3h | ✅ Low |
| 2 | Extract `planner_context.py` | 1h | ✅ Low |
| 3 | Update imports + keep re-exports (temporary) | 2h | ✅ Low |
| 4 | Unit tests + CI pass | 3h | ✅ Low |

### Week 2: Artifact + Payload

| Day | Task | Effort | Risk |
|-----|------|--------|------|
| 1-2 | Extract `artifact_handling.py` | 5h | ✅ Low |
| 3-4 | Extract `payload_builders.py` | 6h | ⚠️ Medium |
| 5 | Add/adjust tests for clamping + payload shaping | 4h | ⚠️ Medium |

### Week 3: Validation + Pause

| Day | Task | Effort | Risk |
|-----|------|--------|------|
| 1-3 | Extract `validation_repair.py` | 8h | ⚠️ Medium |
| 4 | Extract `pause_management.py` | 2h | ✅ Low |
| 5 | Regression tests for repair + pause paths | 4h | ⚠️ Medium |

### Week 4: Orchestration Decomposition

| Day | Task | Effort | Risk |
|-----|------|--------|------|
| 1-3 | Break `_run_loop()` into focused internal helpers | 8h | ⚠️⚠️ High |
| 4 | Optional: extract `memory_integration.py` | 4h | ⚠️ Medium |
| 5 | Cleanup + docs refresh + CI verification | 4h | ✅ Low |

---

## Testing Strategy

Leverage existing coverage and extend it where module boundaries make isolation easier.

### Existing High-Value Coverage (Already in repo)

- Streaming parsing + salvage/repair helpers: `tests/test_react_helpers.py`
- Planner behavior (high-level): `tests/test_react_planner.py`
- LLM client response_format behaviors: `tests/test_llm_client.py`

### Add/Strengthen Tests During Refactor

- `streaming.py`: escape handling + chunk-boundary behavior (keep existing tests; move imports)
- `artifact_handling.py`: artifact/source collection + proxy event emission
- `payload_builders.py`: observation clamping/truncation invariants + payload shaping stability
- `validation_repair.py`: arg-fill parsing edge cases, finish repair parsing, salvage fallbacks
- `_run_loop()` decomposition: regression-focused tests that assert event ordering and exit-path reasons remain stable (don’t try to unit-test every internal helper)

### Coverage Targets

Maintain current project policy (≥85% overall). For newly extracted modules:

| Module | Target Coverage | Notes |
|--------|----------------|------|
| `streaming.py` | 95% | Pure parsing logic |
| `validation_repair.py` | 90% | Error-path heavy |
| `artifact_handling.py` | 85% | Proxy + collectors |
| `payload_builders.py` | 85% | Guardrails + payload |
| `pause_management.py` | 85% | Persistence paths |
| `memory_integration.py` | 80% | Optional tier |

---

## Migration Guide

### For Downstream Developers

No API changes intended. Existing usage continues to work:

```python
from penguiflow.planner import ReactPlanner
```

### For Internal Imports

Private imports from `penguiflow.planner.react` are not part of the public API, but to minimize breakage for internal code/examples:

- Keep temporary re-exports in `penguiflow/planner/react.py` for one release cycle, with deprecation warnings.

Example:

```python
# Before
from penguiflow.planner.react import _StreamingArgsExtractor

# After
from penguiflow.planner.streaming import StreamingArgsExtractor
```

---

## Acceptance Criteria

- [ ] `react.py` reduced substantially (target: ≤1,200 lines; may be revisited after Phase 1–3)
- [ ] Extracted modules have ≥85% test coverage (per-module targets above)
- [ ] No circular dependencies introduced between extracted modules
- [ ] All existing tests pass (ruff, mypy, pytest with 85% gate)
- [ ] No public API changes in `penguiflow/planner/__init__.py`

---

## Appendix: File Size Projections (Order-of-Magnitude)

Current:

```
react.py: 4,442 lines
```

After extraction (approximate; depends on how much `_run_loop()` can be slimmed by delegation):

```
react.py:               ~1,000–1,400 lines (orchestrator + remaining glue)
streaming.py:            ~250–350 lines
planner_context.py:      ~200–250 lines
validation_repair.py:    ~400–650 lines
artifact_handling.py:    ~250–350 lines
payload_builders.py:     ~250–400 lines
pause_management.py:     ~100–200 lines
memory_integration.py:   ~300–450 lines (optional)
```

Net effect: similar total LOC, but distributed across focused, testable modules rather than a single monolith.
