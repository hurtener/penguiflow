# PenguiFlow Playground (dev & release)

## Local development

- Build the precompiled UI assets:
  ```bash
  ./scripts/build_playground_ui.sh
  ```
  (First run installs npm deps and builds to `penguiflow/cli/playground_ui/dist/`.)

- Launch the playground pointing at a project:
  ```bash
  penguiflow dev --project-root . --host 127.0.0.1 --port 8001
  ```
  The command opens the browser automatically; refresh to pick up UI changes (no hot reload).

## Packaging/release checklist

- Ensure `penguiflow/cli/playground_ui/dist/` exists and is fresh (`npm run build`).
- Wheels/SDists ship the built assets only (source excluded via `exclude-package-data`).
- On publish, run `./scripts/build_playground_ui.sh` before `python -m build` to avoid npm at install time.
