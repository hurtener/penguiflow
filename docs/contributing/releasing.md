# Releasing

## What it is / when to use it

This page documents the release process for the PenguiFlow Python package:

- versioning alignment
- changelog updates
- running the CI-equivalent gates locally
- how publishing happens (GitHub Actions)

Use it when preparing a release PR or diagnosing a publishing issue.

## Non-goals / boundaries

- This is not a full incident runbook for PyPI outages.
- This does not define semantic versioning policy; see `VERSIONING.md`.

## Contract surface

### Version sources (must match)

PenguiFlow currently has two version sources that must be kept consistent:

- `pyproject.toml` `project.version`
- `penguiflow/__init__.py` `__version__`

### Publishing mechanism

On push to `main`, CI:

- builds the package (`uv build`)
- publishes to PyPI via trusted publishing (`pypa/gh-action-pypi-publish`)

See `.github/workflows/ci.yml` (`publish` job).

### Docs publishing (GitHub Pages)

Docs are built and deployed from `main` via `.github/workflows/docs.yml`.

## Operational defaults (recommended release steps)

1. Update versions:
   - bump `pyproject.toml`
   - bump `penguiflow/__init__.py`
2. Update `CHANGELOG.md`:
   - move items from “Unreleased” to the new version section
3. Run the CI-equivalent checks locally:

```bash
./scripts/validate_frontend.sh
uv run ruff check .
uv run mypy
uv run pytest --cov=penguiflow --cov-report=term --cov-report=xml --cov-fail-under=84.5
uv run python scripts/check_md_links.py
uv run mkdocs build --strict
```

4. Build the package locally (sanity check):

```bash
uv build
```

5. Merge to `main` to trigger publish + docs deploy.

## Failure modes & recovery

- **Publish job ran but PyPI has old version**: confirm the version bump was merged to `main` and that the publish job succeeded for that commit.
- **Publish job failed**: check `.github/workflows/ci.yml` logs; most failures are packaging issues (missing files, frontend validation).
- **Version mismatch**: CI won’t catch this automatically unless tests assert it; verify both version sources match before merging.
- **Docs deploy failed**: run `mkdocs build --strict` locally; check `.github/workflows/docs.yml` logs.

## Observability

- Release health is visible via GitHub Actions runs (CI + Docs workflows).
- If a release ships a behavior change, ensure observability guidance is updated:
  - runtime events (`FlowEvent`)
  - planner event callback contracts
  - recommended metrics/alerts pages

## Security / multi-tenancy notes

- Publishing uses GitHub OIDC trusted publishing; do not add long-lived PyPI tokens to repo secrets.
- Treat release notes and docs as public surfaces; avoid leaking internal endpoints or sensitive integration details.

## Troubleshooting checklist

- Confirm the commit on `main` contains:
  - version bump in both places,
  - updated changelog,
  - passing CI (including frontend + docs).
