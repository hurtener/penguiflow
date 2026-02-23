# Contributing

Thanks for helping improve PenguiFlow.

## Development setup

Requirements:
- Python 3.11+
- `uv`

```bash
uv venv
uv pip install -e ".[dev]"
```

## Checks

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

## Docs checks (curated site)

```bash
uv pip install -e ".[dev,docs]"
uv run python scripts/check_md_links.py
uv run mkdocs build --strict
```

## Pull requests

- Keep changes focused and well-tested.
- Add or update docs for user-facing behavior changes.
- If you add a feature, include at least one negative/error-path test.

## Code of conduct

By participating, you agree to follow `CODE_OF_CONDUCT.md`.
