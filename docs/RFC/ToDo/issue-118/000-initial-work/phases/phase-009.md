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
