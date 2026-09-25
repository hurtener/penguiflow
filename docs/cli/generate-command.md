# `penguiflow generate`

## What it is / when to use it

`penguiflow generate` turns an **agent spec YAML** into a runnable workspace:

- scaffold a project (via the same template system as `penguiflow new`)
- generate typed tools (Pydantic models + tool functions)
- generate planner wiring and config
- optionally generate flows and orchestrators
- generate tests and environment docs
- persist the spec as `agent.yaml` so the playground can discover it

Use it when you want a spec-first, repeatable “declarative config → code” pipeline.

For ongoing development on a project that already contains implemented tools or custom planner/orchestrator code, use **[`penguiflow apply`](apply-command.md)** instead. `generate` is a bootstrap command; `apply` is the safe reconciliation command.

## Non-goals / boundaries

- This is not a full codegen platform. The generated code is intentionally simple and intended to be edited.
- The spec is not a secrets store; do not put credentials in YAML committed to git.
- `--init` is a workspace bootstrapper; it is not “dry-run safe” (see constraints below).
- Do not use `generate --force` to add a tool to an existing customized project; it can replace generated planner/config/wiring files.

## Contract surface

### Modes

1. Initialize a spec workspace:

```bash
penguiflow generate --init my-agent
```

2. Generate from an existing spec:

```bash
penguiflow generate --spec path/to/my-agent.yaml
```

### Options and constraints

- Exactly one of `--init` or `--spec` is required.
- `--init` cannot be combined with `--spec`.
- `--dry-run` is **not supported** with `--init`.
- `--output-dir` controls where the workspace directory is created (defaults to cwd).
- `--force` overwrites existing files.
- `--verbose` prints a generation summary and progress.

### Spec schema highlights (what’s validated)

The spec is parsed and validated with line-numbered error reporting. Highlights:

- `agent.template` is one of:
  - `minimal`, `react`, `parallel`, `rag_server`, `wayfinder`, `analyst`, `enterprise`
- tool type annotations support:
  - `str`, `int`, `float`, `bool`
  - `Optional[T]`
  - `list[T]`
  - `dict[K,V]` (K must be primitive)
- external tools configuration supports:
  - preset MCP servers (by name), optionally overriding auth and env
  - custom MCP/UTCP connections
- OAuth external tools require HITL (`agent.flags.hitl: true`) and are validated accordingly

## Operational defaults (recommended)

- Treat YAML as *source of truth* for bootstrap and playground discovery.
- After tool implementations exist, use `penguiflow apply --spec agent.yaml --check --diff` for drift detection.
- Keep `agent.yaml` in sync (it is what the playground discovers).

## Runnable examples

### 1) Bootstrap and generate

```bash
uv run penguiflow generate --init my-agent
cd my-agent
# edit my-agent.yaml
uv run penguiflow generate --spec my-agent.yaml --verbose
uv run penguiflow dev --project-root .
```

### 2) Ongoing development: add a tool safely

```bash
# edit agent.yaml and add tools: - name: normalize_data ...
uv run penguiflow apply --spec agent.yaml --check --diff
uv run penguiflow apply --spec agent.yaml
# implement src/<package>/tools/normalize_data.py
```

### 3) Minimal spec excerpt (tools + external tools)

```yaml
agent:
  name: my-agent
  description: "Example agent"
  template: react
  flags:
    hitl: true
    mlflow: true

tools:
  - name: search_documents
    description: "Search documents"
    side_effects: read
    tags: ["search"]
    args:
      query: str
      top_k: Optional[int]
    result:
      documents: list[str]

external_tools:
  presets:
    - preset: github
      auth_override: oauth
      env:
        GITHUB_OWNER: "my-org"
```

## Failure modes & recovery

- **Validation errors**: the CLI reports precise spec paths and line numbers; fix the YAML at the reported location.
- **You used OAuth without HITL**: set `agent.flags.hitl: true` or switch auth to bearer/none.
- **Files skipped**: output already exists; use `--force` intentionally.
- **Generator crashes on templates**: ensure Jinja2 is installed (`penguiflow[cli]`).
- **You need to add a tool after implementation started**: add the tool to `agent.yaml`, run `penguiflow apply`, then implement the new tool file. Existing tool files are preserved.

## Observability

- Use `--verbose` for progress logging and a final summary.
- Generated projects should attach structured logging and event capture as described in:
  - **[Logging](../observability/logging.md)**
  - **[Telemetry patterns](../observability/telemetry-patterns.md)**

## Security / multi-tenancy notes

- Do not store API keys in YAML. Use `.env` (uncommitted) and secret managers.
- Be careful with external tool config: treat tool outputs as untrusted; prefer allowlists and HITL gates for sensitive operations.

## Troubleshooting checklist

- If the playground can’t find your spec, confirm `agent.yaml` exists at the project root (generation writes it).
- If you need to reconcile spec changes without replacing implementation code, use `penguiflow apply`.
- If a markerless wiring file is skipped by `apply`, inspect it manually or run `penguiflow apply --force` only after confirming it is generated code you are willing to refresh.
