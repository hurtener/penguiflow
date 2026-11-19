---
name: dependency-changes-update-uv-lock
description: Use whenever you add, remove or change a dependency, an optional extra or a version constraint in pyproject.toml, or plan such a change. uv.lock changes in the same change, and plans say so explicitly.
---

# Dependency changes update uv.lock

The repo is one uv project with one `uv.lock` at the root. `pyproject.toml` and `uv.lock` change together, in the same change.

**Why:** the quality gates start with `uv sync --locked`, which fails as soon as `uv.lock` no longer matches `pyproject.toml`.

## Rules

1. Change dependencies with uv, from the repo root:
   - `uv add <pkg>` for a runtime dependency (`[project] dependencies`).
   - `uv add --dev <pkg>` for a dev tool (`[dependency-groups] dev`).
   - `uv add --optional <extra> <pkg>` for a published extra (`[project.optional-dependencies]`).
   - `uv remove <pkg>` (with `--dev` or `--optional <extra>` to match) to drop one.

   These update `pyproject.toml` and `uv.lock` together.
2. If you edit `pyproject.toml` by hand, run `uv lock` in the same change.
3. Plans name `uv.lock` as a changed file and say which version it pins now and which it will pin after the change.
4. Verify with `uv lock --check`.
5. A new dependency or extra is at least a minor version bump (see semver-bump).

**How to apply:** after any dependency edit, run `uv lock --check`, then the quality gates, starting with `uv sync --locked`.
