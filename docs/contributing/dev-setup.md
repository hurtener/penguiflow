# Dev setup

## What it is / when to use it

This page is the contributor runbook for setting up a working PenguiFlow development environment:

- editable installs with `uv`
- running lint/typecheck/tests
- building docs locally (MkDocs)

Use it when you are contributing code, docs, or templates to this repository.

## Non-goals / boundaries

- This page does not cover your organization’s Python packaging standards.
- It does not cover production deployment (see **[Deployment](../deployment/overview.md)**).
- It does not teach PenguiFlow concepts; it focuses on contributor workflow.

## Contract surface

### Requirements

- Python **3.11+**
- `uv`

### Dependency groups

- `dev`: ruff, mypy, pytest, etc.
- `docs`: mkdocs + mkdocstrings

In this repo, `uv` uses dependency groups from `pyproject.toml`.

## Operational defaults (recommended)

### 1) Create a venv and install in editable mode

```bash
uv venv
uv pip install -e ".[dev]"
```

### 2) Docs development

```bash
uv pip install -e ".[dev,docs]"
uv run mkdocs serve
```

### 3) Quick local checks

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

## Failure modes & recovery

- **Wrong Python**: ensure your venv uses Python 3.11+ (`python --version` inside the venv).
- **Import errors for optional features**: install the right extras (e.g. `penguiflow[planner]` for ToolNode).
- **UI assets missing for playground** (repo checkout): build under `penguiflow/cli/playground_ui` (`npm install && npm run build`), or run the CI helper script.

## Observability

The repo CI defines the “truth” for passing checks:

- docs strict build + link checking
- lint (ruff), types (mypy), tests (pytest + coverage threshold)

See `.github/workflows/ci.yml`.

## Security / multi-tenancy notes

- Do not commit `.env` files or API keys.
- Treat test fixtures and logs as potentially sensitive if they include tool outputs.

## Troubleshooting checklist

- If a command fails locally but passes CI, confirm:
  - the same Python version,
  - the same dependency groups installed,
  - the same working directory (repo root).
