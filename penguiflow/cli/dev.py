"""CLI entrypoint for `penguiflow dev`."""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import NamedTuple

import uvicorn

from .playground import PlaygroundError, create_playground_app


def _load_env_file(env_path: Path) -> dict[str, str]:
    """Parse .env file and return key-value pairs."""
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Remove surrounding quotes if present
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


class CLIError(Exception):
    """User-facing error for dev command."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class DevServerInfo(NamedTuple):
    host: str
    port: int
    url: str


def _ensure_ui_assets(base_dir: Path) -> None:
    ui_dist = base_dir / "playground_ui" / "dist"
    if not ui_dist.exists():
        raise CLIError(
            "UI assets not found (playground_ui/dist missing).",
            hint="Run `npm install && npm run build` inside penguiflow/cli/playground_ui before packaging.",
        )


def run_dev(*, project_root: Path, host: str, port: int, open_browser: bool) -> DevServerInfo:
    """Create the playground app and run uvicorn."""

    base_dir = Path(__file__).parent
    _ensure_ui_assets(base_dir)

    # Load .env file from project root if it exists
    env_file = project_root / ".env"
    env_vars = _load_env_file(env_file)
    for key, value in env_vars.items():
        if key not in os.environ:  # Don't override existing env vars
            os.environ[key] = value

    try:
        app = create_playground_app(project_root=project_root)
    except PlaygroundError as exc:
        raise CLIError(str(exc)) from exc

    url = f"http://{host}:{port}"
    if open_browser:
        webbrowser.open_new(url)

    print("PenguiFlow playground running:")
    print(f"  UI:    {url}")
    print(f"  API:   {url}/health")
    print("  Tip:   For code changes, refresh the browser (hot-reload not bundled).")

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        server.should_exit = True
        print("\nShutting down playground...")

    return DevServerInfo(host=host, port=port, url=url)


__all__ = ["CLIError", "run_dev", "DevServerInfo"]
