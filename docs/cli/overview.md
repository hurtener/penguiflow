# CLI overview

## What it is / when to use it

PenguiFlow ships a CLI to bootstrap agent projects, run the playground, and validate ToolNode presets:

- `penguiflow new` scaffolds a project from templates
- `penguiflow dev` runs the playground (backend + UI) for a project
- `penguiflow init` generates VS Code helpers (`.vscode/*`)
- `penguiflow generate` generates an agent workspace from a YAML spec
- `penguiflow tools` lists/connects ToolNode presets and can discover tool names
- `penguiflow eval` runs trace-derived eval workflows from committed JSON specs

Use the CLI when you want a repeatable “scaffold → run → iterate” loop and you want to avoid bespoke glue code on day 1.

## Non-goals / boundaries

- The CLI is not a deployment system; production concerns live in **[Deployment](../deployment/overview.md)**.
- The CLI is not a secrets manager; keep secrets in env/secret managers (never in committed YAML).
- `penguiflow tools connect --discover` is a connectivity check, not a production configuration workflow.

## Contract surface

- Installed command: `penguiflow` (see `penguiflow --help`)
- Required extras:
  - `penguiflow new` / `penguiflow generate` require `jinja2` (install `penguiflow[cli]` or `penguiflow[dev]`)
  - `penguiflow tools` discovery requires `penguiflow[planner]`

## Operational defaults (recommended workflows)

### Workflow A: scaffold a project, then run playground

```bash
uv run penguiflow new my-agent --template react
cd my-agent
uv sync
uv run penguiflow dev --project-root .
```

### Workflow B: spec-first generation (declarative)

```bash
uv run penguiflow generate --init my-agent
cd my-agent
# edit my-agent.yaml
uv run penguiflow generate --spec my-agent.yaml
uv run penguiflow dev --project-root .
```

### Workflow C: validate ToolNode connectivity

```bash
uv run penguiflow tools list
uv run penguiflow tools connect github --discover
```

### Workflow D: eval workflow (collect -> review -> evaluate)

```bash
uv run penguiflow eval collect --spec examples/my_agent/datasets/eval_v1/collect.spec.json
# review datasets / define metric
uv run penguiflow eval evaluate --spec examples/my_agent/datasets/eval_v1/evaluate.spec.json
```

For full eval spec fields and output contract, see **[`penguiflow eval`](eval-command.md)**.

## Failure modes & recovery

- **`penguiflow new` fails with “Jinja2 is required”**: install `penguiflow[cli]` (or `jinja2`).
- **`penguiflow dev` fails with missing UI assets** (repo checkout): build UI assets under `penguiflow/cli/playground_ui`.
- **`penguiflow tools` fails with “penguiflow[planner] is required”**: install `penguiflow[planner]`.
- **`penguiflow eval` fails with missing import path**: verify `metric_spec`/`run_one_spec` use `module:callable` and `project_root` points to an importable package.
- **Environment mismatch in playground**: the playground runs in *penguiflow’s* Python environment, not the agent project’s venv (see **[`penguiflow dev`](dev-command.md)**).

## Observability

- `penguiflow dev` runs a local server; use standard logs and `/health` for basic checks.
- Runtime/Planner observability is described in:
  - **[Logging](../observability/logging.md)**
  - **[Telemetry patterns](../observability/telemetry-patterns.md)**

## Security / multi-tenancy notes

- Don’t commit `.env` files; treat them as secrets.
- Keep tool auth in environment variables and secret managers, not in generated code.

## Troubleshooting checklist

- Start by running: `penguiflow --help` and the subcommand `--help`.
- If something works in CI but not locally, validate which venv is running `penguiflow` (`which penguiflow`, `python -c ...`).
