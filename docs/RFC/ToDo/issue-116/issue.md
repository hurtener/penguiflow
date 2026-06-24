# Scaffolded `react` agent omits most `ReactPlanner` configuration knobs

## Summary

When you scaffold a `react` agent with the CLI, the generated `config.py`,
`planner.py`, and `.env.example` only expose a subset of the knobs that
`ReactPlanner` actually accepts. Many simple, single-value settings that are
clearly meant to be tuned per-deployment — reasoning effort, temperature, token
budget, deadlines, retry/repair counts, JSON-schema mode, and more — have **no
spec field, no `Config` attribute, and no environment variable** anywhere in the
scaffold. There is no supported way to set them short of editing generated
source, and that edit does not survive regeneration.

The motivating example is native reasoning: `ReactPlanner` supports
`use_native_reasoning` and `reasoning_effort`, but a freshly scaffolded agent
gives you no way to turn reasoning effort up/down without patching the
generated planner by hand.

## Steps to reproduce

```bash
penguiflow generate --init demo
# edit demo/demo.yaml: set agent.template: react
penguiflow generate --spec demo/demo.yaml

# Look for reasoning / temperature / token-budget knobs — none are present:
grep -rn "reasoning_effort\|temperature\|token_budget" demo/src/*/config.py demo/.env.example
# (no matches)
```

## Expected behavior

A scaffolded `react` agent should let me configure the planner's simple scalar
settings the same way it already exposes `max_iters`, `hop_budget`,
`multi_action_*`, etc. — via the YAML spec, a typed `Config` field, and an
environment variable — without editing generated Python.

## Actual behavior

The generated `ReactPlanner(...)` call wires up only part of the constructor.
Many env-suitable scalar knobs are simply never passed, never appear in
`Config`, and never appear in `.env.example`. To use them you must hand-edit the
generated `planner.py`.

## Evidence

`ReactPlanner.__init__` accepts the kwargs
(`penguiflow/planner/react.py:420-470`), but the generated planner template only
passes a subset (`penguiflow/cli/templates/planner.py.jinja:293-318`). For
example, `use_native_reasoning` / `reasoning_effort` exist on the constructor
(`react.py:445-446`) but are absent from the template, from `spec.py`, from
`config.py.jinja`, and from `env.example.jinja`.

The following **16 simple-scalar, env-suitable** knobs are accepted by
`ReactPlanner` but are not surfaced anywhere in the scaffold:

**LLM-related (7)**

| `ReactPlanner` kwarg | Type / default |
|---|---|
| `temperature` | `float = 0.0` |
| `json_schema_mode` | `bool = True` |
| `llm_timeout_s` | `float = 360.0` |
| `llm_max_retries` | `int = 3` |
| `use_native_reasoning` | `bool = True` |
| `reasoning_effort` | `str \| None = None` |
| `use_native_llm` | `bool = False` |

**Planner-related (9)**

| `ReactPlanner` kwarg | Type / default |
|---|---|
| `token_budget` | `int \| None = None` |
| `pause_enabled` | `bool = True` |
| `deadline_s` | `float \| None = None` |
| `repair_attempts` | `int = 3` |
| `max_consecutive_arg_failures` | `int = 3` |
| `arg_fill_enabled` | `bool = True` |
| `auto_seq_enabled` | `bool = False` |
| `auto_seq_execute` | `bool = False` |
| `auto_seq_read_only_only` | `bool = True` |

## Why hand-editing is not a workaround

`generate --spec` regenerates `config.py`, `planner.py`, and `.env.example` with
`force=True` (`penguiflow/cli/generate.py:1281-1287`), and the safe re-run path
(`apply`) only reconciles managed prompt blocks and tool files. So any knob you
add by hand to the generated planner is silently overwritten the next time the
spec is regenerated and is not part of any supported workflow.

## Impact

- Common production tuning (reasoning effort, temperature, timeouts, token
  budget, deadlines, repair/retry counts) is undiscoverable and effectively
  unavailable in scaffolded agents.
- The knobs *appear* unsupported even though the runtime fully supports them.
- Because the shared `config.py.jinja` / `planner.py.jinja` templates are emitted
  for every spec template (`minimal`, `react`, `parallel`, `rag_server`,
  `wayfinder`, `analyst`, `enterprise`), the gap affects all spec-generated
  agents, not just `react`.

## Notes on scope

This issue is specifically about the **simple single-value (env-able) scalar**
knobs listed above. It does **not** cover:

- Nested/object config blocks (`observation_guardrail`, `error_recovery`,
  `tool_examples`, `llm_fallback`).
- Non-scalar / callable knobs that are not env-configurable
  (`event_callback`, `llm_context_hooks`, `time_source`, `guardrail_gateway`,
  `state_store`), which are expected to be wired in code.
- `guardrail_conversation_history_turns`, which is inert unless the
  (out-of-scope) `guardrail_gateway` object is wired.
