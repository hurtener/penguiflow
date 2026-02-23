# `penguiflow init`

## What it is / when to use it

`penguiflow init` writes a small set of VS Code helper files under `.vscode/`:

- debug launch configurations
- common development tasks (lint, typecheck, tests)
- editor settings tuned for PenguiFlow’s toolchain
- code snippets for common PenguiFlow patterns

Use it when you want “day-1” IDE ergonomics without manually curating `.vscode/*`.

## Non-goals / boundaries

- This command only generates editor config; it does not install dependencies.
- It is VS Code–specific; if you use another editor, treat the output as a reference.
- It won’t overwrite files unless you pass `--force`.

## Contract surface

```bash
penguiflow init [--force] [--dry-run] [--no-launch] [--no-tasks] [--no-settings] [-q/--quiet]
```

Generated files (default):

- `.vscode/launch.json`
- `.vscode/tasks.json`
- `.vscode/settings.json`
- `.vscode/penguiflow.code-snippets`

## Operational defaults (recommended)

- Run once at repo start:

```bash
uv run penguiflow init
```

- Use `--dry-run` in repos with curated editor configs.
- Commit `.vscode/*` only if your org’s standards allow it; otherwise keep local.

## Failure modes & recovery

- **Files skipped**: they already exist; use `--force` if overwriting is intended.
- **Permission errors**: ensure the repo directory is writable.
- **Settings conflict with your team**: re-run with `--no-settings` (or don’t commit settings).

## Observability

This command is pure file I/O; success is visible via created files and CLI output (or `--dry-run` output).

## Security / multi-tenancy notes

- The default settings reference a workspace `.env` file for Python tooling. Do not commit `.env` to git.
- Review generated tasks/launch configs before committing in enterprise repos.

## Troubleshooting checklist

- If VS Code still can’t find tools (`ruff`, `mypy`), verify the project venv is active and the workspace uses the correct interpreter.
- If debugging fails, ensure `debugpy` is available in your environment (usually installed transitively via VS Code Python tooling).
