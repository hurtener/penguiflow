# Spec-Driven Generation (`penguiflow generate`)

This reference covers the declarative path: write a YAML spec, generate the project, regenerate on drift.

## When the spec path wins
- Multiple agents share patterns and you want one source of truth.
- You need CI drift detection (does the generated code match the spec?).
- The team prefers YAML reviews over code reviews of scaffolds.
- You want reproducible scaffolding for tutorials or onboarding.

## When the interactive path wins
- One-off experiment.
- You're still discovering the agent's shape and don't want to maintain a spec.

## Command modes

```bash
penguiflow generate --init <name>           # bootstrap a spec workspace
penguiflow generate --spec <path/to/yaml>   # generate code from a spec
```

Constraints:
- Exactly one of `--init` or `--spec` is required (they are mutually exclusive).
- `--dry-run` is **not** supported with `--init` (bootstrapping always writes).
- `--output-dir` controls workspace location (defaults to cwd).
- `--force` overwrites existing files.
- `--verbose` prints per-file progress and a summary.

## Bootstrap → edit → generate

```bash
# 1) bootstrap: writes <name>/<name>.yaml + a minimal workspace skeleton
uv run penguiflow generate --init my-agent

# 2) edit my-agent/my-agent.yaml — declare tools, flows, services, llm

# 3) generate code from the spec
cd my-agent
uv run penguiflow generate --spec my-agent.yaml --verbose

# 4) run the playground
uv run penguiflow dev --project-root .
```

The generator persists the spec as `agent.yaml` at the project root so the playground can discover it.

## Spec schema (canonical excerpt)

```yaml
agent:
  name: my-agent
  description: Example agent project
  template: react   # minimal|react|parallel|rag_server|wayfinder|analyst|enterprise
  flags:
    streaming: true
    hitl: false
    a2a: false
    memory: true
    background_tasks: false

tools:
  - name: fetch_data
    description: Fetch data from API
    side_effects: read     # pure|read|write|external|stateful
    tags: ["data", "http"]
    group: default
    args:
      query: str
      limit: Optional[int]
    result:
      items: list[str]
    # Optional background-task config
    # background:
    #   enabled: false
    #   mode: job              # job|subagent
    #   default_merge_strategy: HUMAN_GATED   # APPEND|REPLACE|HUMAN_GATED
    #   notify_on_complete: true

flows:
  - name: pipeline
    description: Linear pipeline example
    nodes:
      - name: fetch_data
        description: Fetch data from API
        policy:
          validate: both
          timeout_s: 30
          max_retries: 1
          backoff_base: 0.5
    steps: [fetch_data]

services:
  memory_iceberg:
    enabled: false
    base_url: http://localhost:8000
  rag_server:
    enabled: false
    base_url: http://localhost:8081
  wayfinder:
    enabled: false
    base_url: http://localhost:8082

llm:
  primary:
    model: gpt-4o
    provider: openai

external_tools:
  presets:
    - preset: github
      auth_override: oauth
      env:
        GITHUB_OWNER: "my-org"
```

## Validated type expressions

Tool `args` and `result` accept a restricted type DSL:

- Primitives: `str`, `int`, `float`, `bool`
- Optional: `Optional[T]`
- Lists: `list[T]`
- Dicts: `dict[K, V]` (K must be a primitive)

Composition is recursive (`list[Optional[str]]` is fine; `dict[list[str], int]` is not).

## Validation rules the generator enforces

- `agent.template` ∈ {`minimal`, `react`, `parallel`, `rag_server`, `wayfinder`, `analyst`, `enterprise`}.
- OAuth external tools require `agent.flags.hitl: true`. The validator rejects OAuth + non-HITL combos.
- Tool `name` must be a valid Python identifier.
- `flows[*].steps[]` must reference declared `nodes[].name`s.
- Errors are reported with the spec path and line number.

## What gets generated

For a spec with `template: react`, `--with-streaming` and one tool:

```
my-agent/
  agent.yaml                      # spec mirror (playground discovery)
  pyproject.toml
  README.md
  ENV_SETUP.md
  src/my_agent/
    __init__.py
    __main__.py                   # CLI entry / smoke runner
    tools.py                      # typed tool models + tool fns
    external_tools.py             # MCP/UTCP/HTTP wiring (if external_tools set)
    planner.py                    # ReactPlanner factory
    flows.py                      # flow nodes + orchestrators
  tests/
    test_tools.py
    test_flows.py
```

The orchestrator class name follows `<FlowName>Orchestrator` (PascalCase from `flows[].name`). The bundle class is `<FlowName>FlowBundle`. Errors raised by the flow are `<FlowName>Error`.

## CI drift detection

```yaml
# .github/workflows/spec-drift.yml (example)
- name: Re-generate from spec
  run: uv run penguiflow generate --spec my-agent.yaml --force
- name: Fail on drift
  run: git diff --exit-code
```

This guarantees the spec is the source of truth. Hand-written modules should live outside the generated targets to survive regeneration.

## Failure modes
- **Validation error with line number** → fix the YAML at the reported location.
- **"OAuth requires HITL"** → set `agent.flags.hitl: true` or change the external tool's `auth_override` from `oauth` to `bearer`/`none`.
- **`--init` + `--spec` together** → pick one mode.
- **Files skipped** → target exists; pass `--force` intentionally.
- **Jinja2 missing** → install `penguiflow-lite[cli]`.
- **Playground doesn't discover the spec** → confirm `agent.yaml` exists at the project root (generation writes it; don't delete it).

## Operational defaults
- Commit the YAML; commit generated code only if your org prefers checked-in artifacts.
- Keep generated and hand-written modules in **distinct files** so regeneration is safe.
- Treat external tool outputs as untrusted; use allowlists and HITL gates for sensitive operations.
- Never put secrets in the spec — use `.env` (uncommitted) and secret managers.
