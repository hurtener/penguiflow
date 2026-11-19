# CLI overview

## What it is / when to use it

PenguiFlow ships two small CLIs for validating ToolNode presets and inspecting trace history:

- `penguiflow tools` lists/connects ToolNode presets and can discover tool names (see **[`penguiflow tools`](tools-command.md)**)
- `penguiflow-admin` prints or replays stored trace history from a configured `StateStore`

Use them when you want a quick connectivity or history check without writing bespoke glue code.

## Non-goals / boundaries

- The CLI is not a deployment system; production concerns live in **[Deployment](../deployment/overview.md)**.
- The CLI is not a secrets manager; keep secrets in env/secret managers (never in committed YAML).
- `penguiflow tools connect --discover` is a connectivity check, not a production configuration workflow.

## Contract surface

- Installed commands: `penguiflow` (see `penguiflow --help`) and `penguiflow-admin` (see `penguiflow-admin --help`)
- Required extras:
  - `penguiflow tools` discovery requires `penguiflow-lite[planner]`
- `penguiflow-admin` subcommands:
  - `history <trace_id> --state-store module:callable [--tail N]` prints stored events as JSON lines
  - `replay <trace_id> --state-store module:callable [--tail N] [--delay S]` replays events with an optional delay

## Operational defaults (recommended workflows)

### Workflow A: validate ToolNode connectivity

```bash
uv run penguiflow tools list
uv run penguiflow tools connect github --discover
```

### Workflow B: inspect trace history

```bash
uv run penguiflow-admin history --state-store mypackage.stores:create_store trace_abc123
uv run penguiflow-admin replay --state-store mypackage.stores:create_store trace_abc123 --delay 0.1
```

## Failure modes & recovery

- **`penguiflow tools` fails with “penguiflow-lite[planner] is required”**: install `penguiflow-lite[planner]`.
- **`penguiflow-admin` cannot load the store**: `--state-store` must be an import path (`module:callable`) to a factory returning a `StateStore`, importable from the active venv.

## Observability

- Runtime/Planner observability is described in:
  - **[Logging](../observability/logging.md)**
  - **[Telemetry patterns](../observability/telemetry-patterns.md)**

## Security / multi-tenancy notes

- Don’t commit `.env` files; treat them as secrets.
- Keep tool auth in environment variables and secret managers, not in committed code.

## Troubleshooting checklist

- Start by running: `penguiflow --help` and the subcommand `--help`.
- If something works in CI but not locally, validate which venv is running `penguiflow` (`which penguiflow`, `python -c ...`).
