---
name: semver-bump
description: Use whenever you finish a change to the penguiflow-lite package (penguiflow/, penguiflow_a2a/, examples/, pyproject.toml dependencies). Bump the version by semver with `uv version --bump <major|minor|patch> --no-sync`, which updates pyproject.toml and uv.lock. Never edit versions by hand, and never stage, commit or tag.
---

# Semver bump

The repo is one project, `penguiflow-lite`, published to PyPI. Its version is `version` in the root `pyproject.toml`, also recorded in `uv.lock`. `penguiflow.__version__` reads it from the installed package metadata, so the two can't drift.

**Why:** users pin `penguiflow-lite` by version, and the version is how a release and an installed build are identified.

## Rules

1. After changing the package, bump its version before you finish. Skip changes that touch only `docs/`, `tests/`, `skills/`, `scripts/`, `.agents/` or `AGENTS.md`. `examples/` ships in the wheel, so it counts.
2. Pick the level from the change:
   - `major`: a breaking change. A public name exported from `penguiflow` or `penguiflow_a2a` is removed or renamed, a signature or behavior changes incompatibly, a CLI command or flag or an optional extra is removed, or `requires-python` is raised.
   - `minor`: a compatible new feature, such as a new public API, a new optional parameter, a new CLI command, a new optional extra or a new dependency.
   - `patch`: fixes, refactors, dependency updates, template or config tweaks.
3. Bump at most once per commit. If the version already differs from `HEAD` (`git diff HEAD -- pyproject.toml`), bump again only when the new change needs a higher level. In that case, set the exact version worked out from the `HEAD` version (for example `3.11.2` plus minor is `3.12.0`) instead of bumping twice.
4. Use only these commands, run from the repo root:

   ```sh
   uv version --bump <major|minor|patch> --no-sync   # updates pyproject.toml and uv.lock
   uv version <x.y.z> --no-sync                      # exact version (rule 3)
   ```

5. Never pass `--frozen` to `uv version`. `uv.lock` must carry the new version, or `uv sync --locked` fails.
6. Never edit versions by hand in `pyproject.toml` or `uv.lock`, and never hardcode `__version__` in `penguiflow/__init__.py` again.
7. Never stage, commit or tag the bump. Never run `git tag`.

When done, tell the user the old and new version, along with the changed files.
