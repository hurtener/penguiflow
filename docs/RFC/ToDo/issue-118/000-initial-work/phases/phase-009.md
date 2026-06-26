# Phase 009: Template wiring — all 7 rich-output `new/*` templates + spec-driven root

## Objective
Surface the `ui_component_delivery` flag consistently across every scaffolded surface that builds a rich-output
`ReactPlanner`, following the existing config -> env -> kwarg pattern. Because the flag defaults to `"inline"`,
un-wired templates still work — this phase is about consistency, not correctness. Decisions 8 (all 7 `new/*`
templates incl. `enterprise`) and 9 (spec-driven root in scope).

## Tasks
1. For each of the 7 `new/*` rich-output templates, add `ui_component_delivery` to `config.py.jinja` (field +
   `from_env`), `.env.example`, and the `ReactPlanner(...)` kwarg in `planner.py.jinja` (or `orchestrator.py.jinja`
   for `minimal`).
2. Wire the same into the spec-driven root `penguiflow/cli/templates/planner.py.jinja`.

## Detailed Steps

### Step 1: The 7 `new/*` templates (decision 8 — do NOT omit `enterprise`)
- Templates: `react`, `analyst`, `parallel`, `wayfinder`, `rag_server`, `minimal`, `enterprise`.
- For EACH, under `penguiflow/templates/new/<template>/src/__package_name__/`:
  - `config.py.jinja`: add field `ui_component_delivery: str = "inline"`; in `from_env`, add
    `ui_component_delivery=os.getenv("UI_COMPONENT_DELIVERY", "inline")`.
  - `.env.example`: add `UI_COMPONENT_DELIVERY=inline`.
  - `planner.py.jinja` `build_planner`: pass `ui_component_delivery=config.ui_component_delivery` into
    `ReactPlanner(...)` (alongside `multi_action_*`).
  - EXCEPTION — `minimal` wires the planner via `orchestrator.py.jinja`, not `planner.py.jinja`; add the kwarg
    there.

### Step 2: Spec-driven root (decision 9)
- `penguiflow/cli/templates/planner.py.jinja` is a separate template root rendered by
  `penguiflow/cli/generate.py:822-835` (constructs `ReactPlanner(...)` at `:293-318`). Apply the SAME config/env/kwarg
  pattern there so the flag is consistent across both generation surfaces.

## Required Code

```jinja
{# Target file: penguiflow/templates/new/<template>/src/__package_name__/config.py.jinja  (field) #}
    ui_component_delivery: str = "inline"
```

```jinja
{# Target file: penguiflow/templates/new/<template>/src/__package_name__/config.py.jinja  (from_env) #}
            ui_component_delivery=os.getenv("UI_COMPONENT_DELIVERY", "inline"),
```

```bash
# Target file: penguiflow/templates/new/<template>/.env.example
UI_COMPONENT_DELIVERY=inline
```

```jinja
{# Target file: penguiflow/templates/new/<template>/src/__package_name__/planner.py.jinja  (build_planner) #}
{# (for `minimal`: orchestrator.py.jinja instead) #}
        ui_component_delivery=config.ui_component_delivery,
```

```jinja
{# Target file: penguiflow/cli/templates/planner.py.jinja  (spec-driven root, ReactPlanner(...) ~:293-318) #}
        ui_component_delivery=config.ui_component_delivery,
{# plus the matching config field + from_env + .env.example entry in this template root, mirroring the new/* pattern #}
```

## Exit Criteria (Success)
- [ ] All 7 `new/*` templates (`react`, `analyst`, `parallel`, `wayfinder`, `rag_server`, `minimal`, `enterprise`)
      expose `ui_component_delivery` in `config.py.jinja` (field + `from_env`) and `.env.example`.
- [ ] Each of those templates passes `ui_component_delivery=config.ui_component_delivery` into `ReactPlanner(...)` —
      via `planner.py.jinja` for six of them, via `orchestrator.py.jinja` for `minimal`.
- [ ] `enterprise` is NOT omitted (it builds a rich-output `ReactPlanner` via `planner.py.jinja`).
- [ ] The spec-driven root `penguiflow/cli/templates/planner.py.jinja` also wires the flag (config/env/kwarg).
- [ ] Every scaffolded template defaults to `"inline"`, so a freshly scaffolded project behaves exactly as today
      until the env var is changed.
- [ ] A scaffolded project from each template renders/builds (jinja renders cleanly; no syntax errors in the emitted
      `config.py`/`planner.py`/`orchestrator.py`).
- [ ] Backwards-compat: the existing test suite passes unchanged; scaffolding tests still pass.

## Implementation Notes
- Depends on Phase 000 (the `ReactPlanner` kwarg must exist before templates pass it).
- This phase touches more than 7 files but each edit is a near-identical mechanical 2-line jinja/env change repeated
  across templates; kept as one phase per the approved breakdown (one mechanical pattern). Do NOT split.
- Matches the existing pattern (`config.py.jinja` field -> `from_env` env -> `ReactPlanner(...)` kwarg).
- `penguiflow new` loads every template under `penguiflow.templates.new` (`cli/new.py:123-131`); the spec-driven
  root is distinct (`cli/generate.py`).
- The verified set is `grep -rl "ReactPlanner(" templates/new` ∩ rich-output usage = exactly these 7.

## Verification Commands
```bash
# Confirm the flag is wired in every expected template file
cd /Users/martin.alonso/Documents/lg/repos/penguiflow
grep -rl "ui_component_delivery" penguiflow/templates/new/react penguiflow/templates/new/analyst \
  penguiflow/templates/new/parallel penguiflow/templates/new/wayfinder penguiflow/templates/new/rag_server \
  penguiflow/templates/new/minimal penguiflow/templates/new/enterprise penguiflow/cli/templates/planner.py.jinja

# Confirm enterprise specifically is wired (decision 8 regression guard)
grep -rn "ui_component_delivery" penguiflow/templates/new/enterprise

# Scaffolding / template tests
uv run pytest tests/ -k "template or new or scaffold or generate" -q
uv run ruff check . && uv run mypy
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-06-26

### Summary of Changes
All edits are the same mechanical config -> env -> kwarg pattern. The flag defaults to `"inline"` everywhere, so freshly scaffolded projects behave exactly as before until `UI_COMPONENT_DELIVERY` is changed.

For each of the 7 `new/*` templates under `penguiflow/templates/new/<template>/`:
- `src/__package_name__/config.py.jinja`:
  - Added dataclass field `ui_component_delivery: str = "inline"` (with a 2-line explanatory comment), inserted right after `rich_output_max_total_bytes`.
  - Added `ui_component_delivery=os.getenv("UI_COMPONENT_DELIVERY", "inline"),` to `from_env`, right after the `rich_output_max_total_bytes=...` line.
- `.env.example`: appended `UI_COMPONENT_DELIVERY=inline` after the `LLM_MODEL=...` line.
- `src/__package_name__/planner.py.jinja` `build_planner`: added `ui_component_delivery=config.ui_component_delivery,` into the `ReactPlanner(...)` call after `multi_action_max_tools=...` (for the 6 planner-based templates: `react`, `analyst`, `parallel`, `wayfinder`, `rag_server`, `enterprise`).
- EXCEPTION — `minimal`: the kwarg was added to `src/__package_name__/orchestrator.py.jinja` (which is where `minimal` constructs `ReactPlanner`), not a `planner.py.jinja` (it has none). Config field/`from_env`/`.env.example` were still updated.

Spec-driven root under `penguiflow/cli/templates/`:
- `config.py.jinja`: added the `ui_component_delivery: str = "inline"` field (after `rich_output_max_total_bytes`) and `ui_component_delivery=_env_str("UI_COMPONENT_DELIVERY", "inline"),` in `from_env`.
- `env.example.jinja`: added a documented `UI_COMPONENT_DELIVERY=inline` line in the Rich Output section.
- `planner.py.jinja`: added `ui_component_delivery=config.ui_component_delivery,` into the `ReactPlanner(...)` call (after `multi_action_max_tools=config.planner_multi_action_max_tools,`).

### Key Considerations
- **Literal default vs. spec variable in the spec-driven root.** The other planner settings in `cli/templates/config.py.jinja` are rendered from spec values via Jinja substitution (e.g. `{{ rich_output_max_total_bytes }}`). The `Spec`/`PlannerRichOutputSpec` model (`penguiflow/cli/spec.py`) has NO `ui_component_delivery` field, and the phase does not ask to add one. To honor the plan's explicit instruction ("mirror the new/* pattern" with default `"inline"`) without scope-creeping into the spec schema + `generate.py` context dict, I used a hardcoded literal `"inline"` default in the spec-driven `config.py.jinja` field and `from_env`. The flag is still fully overridable at runtime via the `UI_COMPONENT_DELIVERY` env var (which I added to `env.example.jinja`). See "Deviations" — this is the one place that deviates from pure Jinja-variable style, and it is intentional and minimal.
- **`_env_str` for the spec-driven `from_env`.** The spec-driven `config.py.jinja` already defines and uses `_env_str(...)` for string settings, so I used it there for consistency. The `new/*` templates do not define `_env_str`; they use raw `os.getenv("...", "inline")` for their string settings (e.g. they already import/use `os`), so I matched that local convention there.
- **Anchor robustness.** I anchored every config edit on the `rich_output_max_total_bytes` line (last rich-output field) and every planner edit on `multi_action_max_tools=config.multi_action_max_tools,` followed by the `{% if with_background_tasks %}` block. These anchors are byte-identical across all 7 `new/*` templates (verified before editing), which kept the edits uniform and low-risk.
- **Comment added.** I included a short 2-line comment above each new field documenting the allowed values and the ArtifactStore requirement for store-backed modes. This is slightly beyond the bare 1-line in "Required Code" but aids the scaffolded developer and matches the descriptive style of surrounding config fields. Considered minor and helpful.

### Assumptions
- The `ReactPlanner` kwarg is `ui_component_delivery` with allowed values `"inline" | "both" | "artifact"` and a default of `"inline"` — verified directly in `penguiflow/planner/react_init.py` (it raises `ValueError` for store-backed modes without a real ArtifactStore, and for invalid values).
- Adding the kwarg to the scaffolded `ReactPlanner(...)` calls is safe because the existing scaffolded planners use a `ScriptedLLM` and default `inline` mode (no store required), so construction does not raise. Verified by a runtime smoke test on the generated `react` project: default `inline` builds fine; switching the config field to `"artifact"` (with no store) raises the expected `ValueError`.
- The spec model is intentionally left unchanged (no `ui_component_delivery` field added to `PlannerRichOutputSpec`/`PlannerSpec`); the spec-driven root simply hardcodes the safe `"inline"` default. This preserves backward compatibility of the spec schema (`extra="forbid"` models would reject the unknown key anyway if a user added it; not introducing it avoids any schema-surface change in this phase).
- `templates/new/flow` and `templates/new/controller` are intentionally NOT touched — they are not in the verified set of 7 rich-output `ReactPlanner` builders (decision 8 lists exactly the 7 handled here), consistent with the phase's "verified set" note.

### Deviations from Plan
- **Spec-driven `from_env` uses `_env_str` (not raw `os.getenv`).** The plan's "Required Code" snippet for the new/* templates shows `os.getenv("UI_COMPONENT_DELIVERY", "inline")`. For the spec-driven root I used `_env_str("UI_COMPONENT_DELIVERY", "inline")` to match that template's existing helper-based style. Functionally identical (both return the env value or the `"inline"` default). The new/* templates still use `os.getenv(...)` exactly as specified.
- **Spec-driven default is a literal `"inline"`, not a `{{ ... }}` spec variable.** As explained above, the spec has no such field; this is the minimal way to satisfy "defaults to inline + env-overridable" without a schema change. Everything else follows the plan exactly.
- Added a 2-line clarifying comment above each new config field (the plan showed only the bare field line). No functional impact.

### Potential Risks & Reviewer Attention Points
- **`minimal` is the only template wired via `orchestrator.py.jinja`.** Confirmed the kwarg landed inside the correct `ReactPlanner(...)` call (12-space indentation, after `multi_action_max_tools`). The generated `minimal` project's `orchestrator.py` compiles and contains exactly one `ui_component_delivery=config.ui_component_delivery` occurrence.
- **Spec-driven root with `state_store` providing an artifact store.** The spec-driven `build_planner` already resolves an artifact store (from `state_store.artifact_store` or `_build_artifact_store(config)`); passing `ui_component_delivery=config.ui_component_delivery` interacts with that. With the default `"inline"`, no store is required, so there is no behavior change. If a user sets `UI_COMPONENT_DELIVERY=both|artifact` AND `ARTIFACT_STORE_ENABLED=false` AND no state-store-provided store, `ReactPlanner` will raise `ValueError` at construction — this is the intended library contract, and the new env comment documents the requirement. Reviewer may want to confirm this is the desired UX (it matches the issue's opt-in design).
- **No `.jinja` static analysis.** ruff/mypy do not lint Jinja templates (they aren't valid `.py` pre-render). Coverage of correctness therefore relies on the scaffold-and-compile checks: I scaffolded all 7 `new/*` templates with both `--with-rich-output` and `--with-background-tasks` (exercising both Jinja branches), byte-compiled every emitted `config.py`/`planner.py`/`orchestrator.py`, and additionally generated a project via the spec-driven `run_generate` route and compiled its output. All passed.

### Verification Results
- `grep -rl "ui_component_delivery"` across the 7 `new/*` templates + `cli/templates/planner.py.jinja`: all present (15 files). `enterprise` regression guard: field, from_env, planner kwarg, and `.env.example` all present.
- Scaffolded all 7 `new/*` templates (`--with-rich-output --with-background-tasks`): each emitted `config.py` has 2 occurrences (field + from_env), the 6 planner-based templates have 1 `ui_component_delivery=config.ui_component_delivery` in `planner.py`, `minimal` has 1 in `orchestrator.py`, and every `.env.example` has `UI_COMPONENT_DELIVERY=inline`. All emitted files byte-compile.
- Runtime smoke test (generated `react` project): `Config.from_env().ui_component_delivery == "inline"`; `build_planner` yields a planner with `_ui_component_delivery == "inline"`; setting the field to `"artifact"` without a store raises the expected `ValueError`.
- Spec-driven `run_generate`: generated `config.py`/`planner.py`/`.env.example` contain the flag and compile cleanly.
- `uv run pytest tests/ -k "template or new or scaffold or generate" -q`: 90 passed.
- `uv run pytest tests/cli`: exit code 0 (all passed).
- `uv run ruff check .`: All checks passed.
- `uv run mypy`: Success, no issues found in 228 source files (only a pre-existing informational note in `penguiflow_a2a/bindings/http.py`).

### Files Modified
- `penguiflow/templates/new/react/.env.example`
- `penguiflow/templates/new/react/src/__package_name__/config.py.jinja`
- `penguiflow/templates/new/react/src/__package_name__/planner.py.jinja`
- `penguiflow/templates/new/analyst/.env.example`
- `penguiflow/templates/new/analyst/src/__package_name__/config.py.jinja`
- `penguiflow/templates/new/analyst/src/__package_name__/planner.py.jinja`
- `penguiflow/templates/new/parallel/.env.example`
- `penguiflow/templates/new/parallel/src/__package_name__/config.py.jinja`
- `penguiflow/templates/new/parallel/src/__package_name__/planner.py.jinja`
- `penguiflow/templates/new/wayfinder/.env.example`
- `penguiflow/templates/new/wayfinder/src/__package_name__/config.py.jinja`
- `penguiflow/templates/new/wayfinder/src/__package_name__/planner.py.jinja`
- `penguiflow/templates/new/rag_server/.env.example`
- `penguiflow/templates/new/rag_server/src/__package_name__/config.py.jinja`
- `penguiflow/templates/new/rag_server/src/__package_name__/planner.py.jinja`
- `penguiflow/templates/new/minimal/.env.example`
- `penguiflow/templates/new/minimal/src/__package_name__/config.py.jinja`
- `penguiflow/templates/new/minimal/src/__package_name__/orchestrator.py.jinja`
- `penguiflow/templates/new/enterprise/.env.example`
- `penguiflow/templates/new/enterprise/src/__package_name__/config.py.jinja`
- `penguiflow/templates/new/enterprise/src/__package_name__/planner.py.jinja`
- `penguiflow/cli/templates/config.py.jinja`
- `penguiflow/cli/templates/env.example.jinja`
- `penguiflow/cli/templates/planner.py.jinja`
