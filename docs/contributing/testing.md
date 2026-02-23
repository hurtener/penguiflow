# Testing

## What it is / when to use it

This page documents the contributor testing workflow for PenguiFlow:

- unit tests
- lint and types
- coverage requirements
- docs checks (strict MkDocs + link hygiene)

Use it before opening a PR and when debugging CI failures.

## Non-goals / boundaries

- This page does not define product-level QA or benchmarking.
- It does not cover integration tests requiring real network access or external credentials (avoid those in CI).

## Contract surface

### CI matrix and enforced checks

CI runs on:

- Python: 3.11, 3.12, 3.13
- OS: Ubuntu

Checks enforced before merge:

- `ruff` (lint)
- `mypy` (types)
- `pytest` with coverage threshold
- docs gates:
  - `mkdocs build --strict`
  - `python scripts/check_md_links.py`
- frontend validation for playground UI assets (`./scripts/validate_frontend.sh`)

See `.github/workflows/ci.yml`.

## Operational defaults (recommended local commands)

### Unit tests

```bash
uv run pytest
```

### Lint + types

```bash
uv run ruff check .
uv run mypy
```

### Coverage (CI-equivalent)

```bash
uv run pytest --cov=penguiflow --cov-report=term --cov-report=xml --cov-fail-under=84.5
```

### Docs gates

```bash
uv run python scripts/check_md_links.py
uv run mkdocs build --strict
```

### Playground UI validation (repo checkout)

```bash
./scripts/validate_frontend.sh
```

## Failure modes & recovery

- **Flaky tests**: isolate the failing test, remove timing assumptions, and ensure no external network calls.
- **Coverage regression**: add at least one negative/error-path test for new behavior (policy target is ≥85%).
- **Docs build fails under `--strict`**: fix broken links or missing pages; avoid linking from curated docs to excluded/internal docs.
- **Frontend validation fails**: rebuild UI assets under `penguiflow/cli/playground_ui` and re-run.

## Observability

- Use `pytest -vv -s` for more output during debugging.
- Prefer targeted test runs (single file) before full suite when iterating locally.

## Security / multi-tenancy notes

- Never run tests that require real API keys in CI.
- Avoid recording real tool payloads in golden snapshots; keep fixtures synthetic and redacted.

## Troubleshooting checklist

- If CI fails but local passes:
  - verify Python version,
  - run the exact CI commands above,
  - confirm you installed the same dependency groups (`.[dev]`, `.[docs]` when needed).
