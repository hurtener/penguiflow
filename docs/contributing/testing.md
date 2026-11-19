# Testing

## What it is / when to use it

This page documents the contributor testing workflow for PenguiFlow:

- unit tests
- lint and types
- coverage requirements

Use it before opening a PR and when a check fails.

## Non-goals / boundaries

- This page does not define product-level QA or benchmarking.
- It does not cover integration tests requiring real network access or external credentials (keep those out of the test suite).

## Contract surface

### Required checks

Run these locally on Python 3.11+ before opening a PR:

- `ruff` (lint)
- `mypy` (types)
- `pytest` with the coverage threshold (`fail_under` in `pyproject.toml`)

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

### Coverage

```bash
uv run pytest --cov=penguiflow --cov-report=term --cov-report=xml --cov-fail-under=84.5
```

## Failure modes & recovery

- **Flaky tests**: isolate the failing test, remove timing assumptions, and ensure no external network calls.
- **Coverage regression**: add at least one negative/error-path test for new behavior (policy target is ≥85%).

## Observability

- Use `pytest -vv -s` for more output during debugging.
- Prefer targeted test runs (single file) before full suite when iterating locally.

## Security / multi-tenancy notes

- Never add tests that require real API keys; the suite must pass offline.
- Avoid recording real tool payloads in golden snapshots; keep fixtures synthetic and redacted.

## Troubleshooting checklist

- If a check passes for one contributor but fails for another:
  - verify Python version,
  - run the exact commands above,
  - confirm you installed the same dependency groups (`.[dev]`).
