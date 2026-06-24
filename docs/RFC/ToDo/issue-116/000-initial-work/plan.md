# Plan: Expand scaffolded `config.py` to cover all simple-scalar ReactPlanner knobs (with single-source docs)

## Context

When you scaffold a `react` agent via `penguiflow generate --init [name]` → set `agent.template: react` → `penguiflow generate --spec [name].yaml`, the generated `config.py` + `planner.py` only wire up ~40% of `ReactPlanner`'s 47 constructor knobs. Confirmed by reading the code:

- `ReactPlanner.__init__` accepts `use_native_reasoning` / `reasoning_effort` (`penguiflow/planner/react.py:445-446`), but the generated `planner.py` (`penguiflow/cli/templates/planner.py.jinja:293-318`) never passes them, and there is no spec field, no `Config` field, and no env var for them anywhere.
- `generate --spec` regenerates `config.py`/`planner.py` with `force=True`, and the safe re-run path `apply` only reconciles managed prompt blocks + tool files — so hand-edits to add a knob don't survive and aren't supported.

**Decision (from clarifying questions):** Reasoning is just the first example; the goal is to make the scaffold richer for **any** missing knob, using **typed per-knob plumbing** (no magic passthrough), scoped to the **"all simple scalars"** set — every env-able single-value knob, **16 bool/int/float/str knobs** in total. We are **not** solving non-destructive regen (you scaffold once and hand-maintain), and we are **not** adding nested config blocks (`observation_guardrail`, `error_recovery`, `tool_examples`, `llm_fallback`) or non-scalar/object knobs (callbacks, stores, hooks, gateways stay wired in code).

**Outcome:** A freshly-scaffolded react agent exposes these 16 knobs end-to-end (YAML spec → `config.py` dataclass + `from_env()` → env var → `ReactPlanner(...)`), all backward-compatible (defaults match current ReactPlanner defaults, so existing specs behave identically). In addition, every planner-wired knob is **documented from a single source** (a `description=` on its spec field) that the generator fans out into IDE/LLM-visible attribute docstrings in `config.py` and comments in `.env.example`; the non-planner fields (toggles, service URLs) get plain hand-written docstrings — see §8.

> Note: `generate --spec` always emits the **shared** `cli/templates/config.py.jinja` and `cli/templates/planner.py.jinja` for **all 7 spec templates** (`minimal`, `react`, `parallel`, `rag_server`, `wayfinder`, `analyst`, `enterprise`) — `_generate_config`/`_generate_planner` run unconditionally and force-overwrite. So editing these two shared templates covers every spec-generated agent at once. (Bare `penguiflow new` per-template configs are a separate, out-of-scope sweep.)

## Clarifications resolved (verify-plan, 2026-06-23)

These four decisions were confirmed against the codebase and are binding for the implementer; the sections below already reflect them:

1. **Descriptions for the 4 knobs added beyond the originally-documented 12** (`use_native_llm`, `auto_seq_enabled`, `auto_seq_execute`, `auto_seq_read_only_only`): `penguiflow_config_knobs.md` only documents the original 12 NEW knobs. The implementer must **author concise descriptions for these 4 in `spec.py` (`Field(..., description=…)`) AND extend `penguiflow_config_knobs.md` to all 16 NEW rows** so the doc stays the true single source. (See §1, §8.)
2. **`llm_model` docstring:** `_field_descriptions()` introspects only the **top-level** `LLMSpec`/`PlannerSpec` fields, which cannot reach the nested `LLMPrimarySpec.model` (`spec.py:479`). So `llm_model` gets a **hand-written inline docstring** (the "#1" path) and `primary.model` is **dropped from the single-source set** — do **not** add a `description=` to `primary.model` and do **not** special-case nested introspection. (See §1, §8.)
3. **`guardrail_conversation_history_turns` (`int = 1`, `react.py:464`) stays excluded.** Although it is a plain int scalar, it is inert unless the out-of-scope `guardrail_gateway` object is wired. The knob set remains exactly **16**. (See Out of scope.)
4. **`.env.example` for the 3 optionals** (`LLM_REASONING_EFFORT`, `PLANNER_TOKEN_BUDGET`, `PLANNER_DEADLINE_S`): emit them **uncommented with the spec value when the spec sets a non-default** (non-`None`), and **commented/empty** (`# PLANNER_TOKEN_BUDGET=`) when the spec leaves them at the `None` default. This differs from the always-commented `SHORT_TERM_MEMORY_SUMMARIZER_MODEL` convention and requires conditional rendering in `env.example.jinja` plus the actual (un-repr'd) value threaded through the `_generate_env_example` context. (See §2, §5.)

## The 16 knobs and their plumbing

Two groups, matching the existing convention (`spec.llm.*` → `config.llm_*`; `spec.planner.*` → `config.planner_*`):

**LLM group** (add fields to `LLMSpec`, `penguiflow/cli/spec.py:493`; config defaults sourced in `_generate_config`):

| ReactPlanner kwarg | Type / default | `config.py` field | Env var | `from_env` helper |
|---|---|---|---|---|
| `temperature` | `float = 0.0` | `llm_temperature` | `LLM_TEMPERATURE` | `_env_float` |
| `json_schema_mode` | `bool = True` | `llm_json_schema_mode` | `LLM_JSON_SCHEMA_MODE` | `_env_flag` |
| `llm_timeout_s` | `float = 360.0` | `llm_timeout_s` | `LLM_TIMEOUT_S` | `_env_float` |
| `llm_max_retries` | `int = 3` | `llm_max_retries` | `LLM_MAX_RETRIES` | `_env_int` |
| `use_native_reasoning` | `bool = True` | `llm_use_native_reasoning` | `LLM_USE_NATIVE_REASONING` | `_env_flag` |
| `reasoning_effort` | `str \| None = None` | `llm_reasoning_effort` | `LLM_REASONING_EFFORT` | `_env_optional_str` |
| `use_native_llm` | `bool = False` | `llm_use_native_llm` | `LLM_USE_NATIVE_LLM` | `_env_flag` |

**Planner group** (add fields to `PlannerSpec`, `penguiflow/cli/spec.py:803`):

| ReactPlanner kwarg | Type / default | `config.py` field | Env var | `from_env` helper |
|---|---|---|---|---|
| `token_budget` | `int \| None = None` | `planner_token_budget` | `PLANNER_TOKEN_BUDGET` | `_env_optional_int` *(new)* |
| `pause_enabled` | `bool = True` | `planner_pause_enabled` | `PLANNER_PAUSE_ENABLED` | `_env_flag` |
| `deadline_s` | `float \| None = None` | `planner_deadline_s` | `PLANNER_DEADLINE_S` | `_env_optional_float` *(new)* |
| `repair_attempts` | `int = 3` | `planner_repair_attempts` | `PLANNER_REPAIR_ATTEMPTS` | `_env_int` |
| `max_consecutive_arg_failures` | `int = 3` | `planner_max_consecutive_arg_failures` | `PLANNER_MAX_CONSECUTIVE_ARG_FAILURES` | `_env_int` |
| `arg_fill_enabled` | `bool = True` | `planner_arg_fill_enabled` | `PLANNER_ARG_FILL_ENABLED` | `_env_flag` |
| `auto_seq_enabled` | `bool = False` | `planner_auto_seq_enabled` | `PLANNER_AUTO_SEQ_ENABLED` | `_env_flag` |
| `auto_seq_execute` | `bool = False` | `planner_auto_seq_execute` | `PLANNER_AUTO_SEQ_EXECUTE` | `_env_flag` |
| `auto_seq_read_only_only` | `bool = True` | `planner_auto_seq_read_only_only` | `PLANNER_AUTO_SEQ_READ_ONLY_ONLY` | `_env_flag` |

(7 LLM-group + 9 planner-group = 16. All four additions over the original 12 are plain `bool` → existing `_env_flag`, no new helpers.)

## Files to modify

### 1. `penguiflow/cli/spec.py` — add typed spec fields
- **`LLMSpec`** (line 493): add `temperature`, `json_schema_mode`, `timeout_s`, `max_retries`, `use_native_reasoning`, `use_native_llm` (with defaults above) and `reasoning_effort: str | None = None`. Add a light `field_validator` for `reasoning_effort` mirroring the existing `summarizer_model` validator (`spec.py:580-587`): allow `None`, else strip and reject empty. Keep it permissive (`str | None`, not a hard `Literal`) so non-OpenAI providers aren't blocked.
- **`PlannerSpec`** (line 803): add `token_budget: int | None = None`, `pause_enabled: bool = True`, `deadline_s: float | None = None`, `repair_attempts: int = 3`, `max_consecutive_arg_failures: int = 3`, `arg_fill_enabled: bool = True`, `auto_seq_enabled: bool = False`, `auto_seq_execute: bool = False`, `auto_seq_read_only_only: bool = True`. Reuse the existing non-negative-int validator pattern for the int fields where sensible.
- Both models use `extra="forbid"`, but adding fields **with defaults** is backward-compatible: existing specs lacking these keys still validate and get current ReactPlanner defaults.
- **Give every new field a `Field(default, description="…")`** (instead of a bare default), and backfill `description=` on the existing **top-level** planner/LLM fields that back a `ReactPlanner(...)` arg (`max_iters`, `hop_budget`, `absolute_max_parallel`, `stream_final_response`, `multi_action_*`). These descriptions are the **single source** for the generated docstrings/comments — see §8. **Do not** add `description=` to the nested `primary.model` — `llm_model` is documented via a hand-written docstring instead (decision 2; §8).
- **Author descriptions for the 4 knobs missing from the doc** (`use_native_llm`, `auto_seq_enabled`, `auto_seq_execute`, `auto_seq_read_only_only`): write concise `description=` text for them in `spec.py` (decision 1). The other 12 new knobs reuse the wording in `penguiflow_config_knobs.md`. Then update that doc — see §8.

### 2. `penguiflow/cli/generate.py` — feed defaults into the template context
- **`_generate_config`** (line 822 context dict): add the 16 keys. Scalars pass through directly (e.g. `"llm_temperature": spec.llm.temperature`, `"llm_use_native_llm": spec.llm.use_native_llm`, `"planner_auto_seq_enabled": spec.planner.auto_seq_enabled`). The three optionals use `repr(...)` so the jinja renders a valid literal (`None` or a quoted value), matching the existing `short_term_memory_summarizer_model` pattern (`generate.py:905-907`): e.g. `"llm_reasoning_effort": repr(spec.llm.reasoning_effort) if spec.llm.reasoning_effort is not None else "None"`, and `"planner_token_budget": spec.planner.token_budget if spec.planner.token_budget is not None else "None"` (same for `deadline_s`).
- **`_generate_env_example`** (line 966 context dict): also add the 16 keys here — this generator has its **own** context dict, separate from `_generate_config`. For env rendering, pass the **actual values** (not `repr`'d): bool/int/float/str scalars as-is (match the existing lowercased-bool style used for the other env flags, e.g. `str(bool(...)).lower()`), and the **three optionals as their real value or `None`** (`"llm_reasoning_effort": spec.llm.reasoning_effort`, `"planner_token_budget": spec.planner.token_budget`, `"planner_deadline_s": spec.planner.deadline_s`) so the jinja `{% if x is not none %}` conditional (decision 4; §5) can emit them uncommented-with-value or commented-empty.
- **`_generate_planner`** needs **no changes** — `planner.py.jinja` references `config.*` at runtime, not jinja vars.

### 3. `penguiflow/cli/templates/config.py.jinja` — dataclass fields + `from_env` + 2 helpers
- Add two helpers next to the existing ones (after `_env_optional_str`, line 45): `_env_optional_int(name, default)` and `_env_optional_float(name, default)` (return `default` if unset/blank, else parse).
- Add a `# LLM Request Settings` block under the existing LLM section (after line 65) with the 7 LLM fields, and extend the `# Planner Settings` block (after line 92) with the 9 planner fields. Optionals render via the repr'd jinja var: `llm_reasoning_effort: str | None = {{ llm_reasoning_effort }}`, `planner_token_budget: int | None = {{ planner_token_budget }}`, `planner_deadline_s: float | None = {{ planner_deadline_s }}`.
- Add matching `from_env()` entries using the helper column above.
- Emit an **attribute docstring** under every field (a `"""…"""` literal on the next line — Pylance/Pyright render these on hover). Planner-wired knobs pull their text from the description map (§8); non-planner fields (the derived toggles, the three service URLs, `rich_output_allowlist`) get short hand-written docstrings.

### 4. `penguiflow/cli/templates/planner.py.jinja` — pass the kwargs
- In the `ReactPlanner(...)` call (lines 293-318), add the 16 kwargs reading from `config.*` (e.g. `temperature=config.llm_temperature, ... use_native_llm=config.llm_use_native_llm, reasoning_effort=config.llm_reasoning_effort, token_budget=config.planner_token_budget, pause_enabled=config.planner_pause_enabled, deadline_s=config.planner_deadline_s, repair_attempts=config.planner_repair_attempts, max_consecutive_arg_failures=config.planner_max_consecutive_arg_failures, arg_fill_enabled=config.planner_arg_fill_enabled, auto_seq_enabled=config.planner_auto_seq_enabled, auto_seq_execute=config.planner_auto_seq_execute, auto_seq_read_only_only=config.planner_auto_seq_read_only_only`). Unconditional — no `{% if %}` guards.

### 5. `penguiflow/cli/templates/env.example.jinja` — document the env vars
- Add the 7 `LLM_*` vars to the LLM section (after line 31) and the 9 `PLANNER_*` vars to the Planner Settings section (after line 62), following the `{{ var }}` default style.
- **The three optionals (`LLM_REASONING_EFFORT`, `PLANNER_TOKEN_BUDGET`, `PLANNER_DEADLINE_S`) render conditionally** (decision 4b): when the spec sets a non-default (the threaded value is not `None`) emit it **uncommented with the value** (`PLANNER_TOKEN_BUDGET={{ planner_token_budget }}`); otherwise emit the **commented-empty** form (`# PLANNER_TOKEN_BUDGET=`). Use a `{% if planner_token_budget is not none %}…{% else %}…{% endif %}` block per optional (the value is threaded raw, not repr'd — §2). The 13 non-optional new vars render plainly via `{{ var }}`.
- Prefix each new planner-wired var with a `# {description}` comment sourced from the **same** description map (§8), so the env file and the dataclass docstrings can't drift.

### 6. `penguiflow/cli/templates/init/sample_spec.yaml.jinja` — discoverability (recommended)
- Add commented hints under `llm:` (after line ~30) and `planner:` (after line 132) showing the new knobs with defaults, e.g. `# temperature: 0.0`, `# reasoning_effort: high   # low|medium|high (provider-dependent)`, `# token_budget: 60000`, `# pause_enabled: true`. This is the discoverability fix for the original complaint — users see the knobs exist without reading source.

### 7. Tests — extend `tests/cli/test_generate_e2e.py`
- In `test_run_generate_creates_planner_and_tools` (line 182): add asserts that `config_content` contains the new fields (e.g. `llm_temperature`, `llm_reasoning_effort`, `planner_token_budget`), `planner_content` contains the new kwargs (e.g. `reasoning_effort=config.llm_reasoning_effort`), and `env_content` contains e.g. `LLM_TEMPERATURE` / `PLANNER_REPAIR_ATTEMPTS`.
- Add a focused test that writes a spec with non-default values (`llm.reasoning_effort: high`, `llm.temperature: 0.3`, `planner.token_budget: 50000`) and asserts those literals appear in the generated `config.py` defaults — proving the spec→config flow, not just template presence.
- Extend the existing `tests/cli/test_spec_validation.py` (the file already exists) with a case asserting the new fields parse and that bad values (empty `reasoning_effort`) are rejected.
- Assert documentation rendered: a planner knob's description appears as a docstring in generated `config.py` (e.g. the `reasoning_effort` description text) and as a `#` comment in `.env.example`, proving the single-source map (§8) flows to both.

### 8. Documentation — single source for planner knobs (#3), plain docstrings for the rest (#1)
Goal: every `Config` attribute carries a description that IDEs (Pylance/Pyright hover) and LLMs can read; the planner-wired knobs are documented from **one** place so config docstrings and `.env` comments can never drift.
- **Single source = `spec.py`** (done in step 1): each planner-backed spec field carries `Field(..., description="…")`. Why this is clean: as confirmed during planning, the messy non-1:1 fields (the derived `*_enabled` toggles, `with_background_tasks`, the three `*_base_url`s) are **not** `ReactPlanner` constructor params — so (almost) every actual planner knob maps to a real **top-level** spec field, and #3 applies with no special-casing. **The one exception is `llm_model`**, which is backed by the nested `LLMPrimarySpec.model`; per decision 2 it is documented via a hand-written docstring (#1 below), not the single-source map.
- **Generator helper (`generate.py`):** add `_field_descriptions()` that introspects **only the top-level** `LLMSpec`/`PlannerSpec` fields via `model_fields[name].description` and returns a `{config_attr: description}` map (applying the `llm_`/`planner_` prefix rename; **skip fields whose `.description` is `None`** so nested sub-models like `primary`/`summarizer`/`reflection` and undescribed fields are excluded). Do **not** recurse into nested models. Thread the map into both the `_generate_config` and `_generate_env_example` template contexts.
- **Render targets:** `config.py.jinja` (attribute docstrings, step 3) and `env.example.jinja` (leading `#` comments, step 5) both read from this one map.
- **Non-planner fields get #1:** the derived toggles (`memory_enabled`, `summarizer_enabled`, `reflection_enabled`, `short_term_memory_enabled`), the service URLs, `rich_output_allowlist`, and **`llm_model`** (decision 2 — nested backing field) get short hand-written docstrings inline in `config.py.jinja` — they have no single top-level backing spec field, so single-sourcing them adds machinery for no payoff.
- **Scope note:** the deeply-nested groups (`tool_search_*`, `skills_*`, `short_term_memory_*`, `artifact_store_*`, `background_tasks_*`) can be brought under the same `description=` source later via nested introspection; for this pass they get plain (#1) docstrings. The 16 new knobs + the top-level planner knobs are the #3 target.
- **Description source text:** reuse the wording already written in `penguiflow_config_knobs.md` (repo root) for the original 12 NEW knobs. **Author concise new wording for the 4 knobs missing from the doc** (`use_native_llm`, `auto_seq_enabled`, `auto_seq_execute`, `auto_seq_read_only_only`; decision 1).
- **Update `penguiflow_config_knobs.md` to all 16 NEW rows** (add the 4 missing knobs to the LLM/Planner tables and flip the legend from "12 knobs proposed (6 `LLM_*`, 6 `PLANNER_*`)" to 16 (7 `LLM_*`, 9 `PLANNER_*`)), so the doc remains the true reference for the shipped knob set (decision 1).

## Verification

```bash
# Lint + type + the full CLI generation suite
uv run ruff check penguiflow/cli
uv run mypy penguiflow/cli
uv run pytest tests/cli/test_generate_e2e.py tests/cli/test_spec_validation.py -q

# End-to-end smoke: scaffold a react agent and confirm the knobs landed
cd "$(mktemp -d)"
uv run penguiflow generate --init demo
# edit demo/demo.yaml: agent.template -> react (and optionally set llm.reasoning_effort: high)
uv run penguiflow generate --spec demo/demo.yaml
grep -n "llm_reasoning_effort\|planner_token_budget\|llm_temperature" demo*/src/*/config.py
grep -n "reasoning_effort=config\|token_budget=config\|arg_fill_enabled=config" demo*/src/*/planner.py
grep -n "LLM_REASONING_EFFORT\|PLANNER_TOKEN_BUDGET" demo*/.env.example
# import-time sanity: Config.from_env() builds and the planner constructs
LLM_REASONING_EFFORT=high uv run python -c "import sys; sys.path.insert(0,'demo*/src'); ..."  # or run the generated tests
```

Backward-compat check: regenerate an existing spec that lacks the new keys → it must still succeed and produce defaults identical to today's ReactPlanner behavior.

## Out of scope (deliberately)
- Nested config blocks (`observation_guardrail`, `error_recovery`, `tool_examples`, `llm_fallback`) — would be the "scalars + nested blocks" tier.
- Non-scalar/object knobs (`event_callback`, `llm_context_hooks`, `time_source`, `guardrail_gateway`, `state_store`) — not env-configurable; stay in code.
- `guardrail_conversation_history_turns` (`int = 1`, `react.py:464`) — although a plain int scalar, it is inert unless the out-of-scope `guardrail_gateway` object is wired, so it is **deliberately excluded** (decision 3). The knob set stays at exactly 16.
- Non-destructive regeneration (`apply` reconciling `config.py` / the `ReactPlanner(...)` call) — not needed for the scaffold-once workflow.
- Cleanup of the vestigial `rag_server_base_url` / `wayfinder_base_url` config in non-rag/non-wayfinder agents, and the bare-`penguiflow new` per-template `config.py`/`planner.py` sweep — separate tasks.
