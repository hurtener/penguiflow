"""CLI entrypoint for `penguiflow dev`."""

from __future__ import annotations

import importlib
import logging
import os
import sys
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple, cast

import uvicorn

from .playground import PlaygroundError, create_playground_app

_LOGGER = logging.getLogger(__name__)


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


_MEMORY_BASE_URL_ALIASES = ("PLATFORM_URL",)


@contextmanager
def _memory_base_url_compat_env() -> Iterator[None]:
    """Temporary compatibility shim for legacy project state-store builders.

    `penguiflow dev` relies on `MEMORY_BASE_URL` as the user-facing setting.
    During builder invocation only, mirror this value to legacy alias keys when
    those keys are absent.
    """
    memory_url = os.getenv("MEMORY_BASE_URL")
    inserted_keys: list[str] = []

    if memory_url:
        for key in _MEMORY_BASE_URL_ALIASES:
            if key in os.environ:
                continue
            inserted_keys.append(key)
            os.environ[key] = memory_url

    try:
        yield
    finally:
        for key in inserted_keys:
            os.environ.pop(key, None)


@contextmanager
def _memory_base_url_compat_builder_patch(module: Any) -> Iterator[None]:
    """Patch legacy env helpers to honor ``MEMORY_BASE_URL`` aliasing.

    Some generated builders call a local ``from_env_or_dotenv`` helper that can
    ignore process env values when running locally. This patch ensures alias
    keys resolve from ``MEMORY_BASE_URL`` as a fallback during factory calls.
    """
    original = getattr(module, "from_env_or_dotenv", None)
    memory_url = os.getenv("MEMORY_BASE_URL")
    if not callable(original) or not memory_url:
        yield
        return
    original_fn = cast(Callable[[str, str], str], original)

    def _patched(env_var_name: str, default: str) -> str:
        if env_var_name in _MEMORY_BASE_URL_ALIASES:
            # Prefer process env prepared by `run_dev` over any cwd-scoped
            # dotenv reads inside legacy helpers.
            env_value = os.getenv(env_var_name)
            if env_value:
                return env_value
            return memory_url
        value = original_fn(env_var_name, default)
        return value

    module.from_env_or_dotenv = _patched
    try:
        yield
    finally:
        module.from_env_or_dotenv = original_fn


def _load_project_state_store(project_root: Path) -> Any | None:
    """Try to build a project-provided state store from env.

    Lookup order:
    1) agentiv.state_store_enhanced.build_agentiv_enhanced_state_store_from_env
    2) agentiv.state_store.build_agentiv_state_store_from_env
    """
    src_dir = project_root / "src"
    search_root = src_dir if src_dir.exists() else project_root
    root_str = str(search_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    candidates = [
        ("agentiv.state_store_enhanced", "build_agentiv_enhanced_state_store_from_env"),
        ("agentiv.state_store", "build_agentiv_state_store_from_env"),
    ]

    for module_name, factory_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        except Exception as exc:
            _LOGGER.debug("state_store_module_import_failed", exc_info=exc)
            continue
        factory = getattr(module, factory_name, None)
        if not callable(factory):
            continue
        try:
            with _memory_base_url_compat_env(), _memory_base_url_compat_builder_patch(module):
                store = factory()
            _LOGGER.info(
                "playground_state_store_loaded",
                extra={"module": module_name, "factory": factory_name, "type": type(store).__name__},
            )
            return store
        except Exception as exc:
            _LOGGER.debug(
                "state_store_factory_failed",
                extra={"module": module_name, "factory": factory_name},
                exc_info=exc,
            )
            continue
    return None


def _describe_state_store(store: Any) -> tuple[str, str | None]:
    """Return store type and best-effort base URL for diagnostics."""
    store_type = type(store).__name__
    # Common pattern: httpx.AsyncClient stored as `_client`.
    client = getattr(store, "_client", None)
    base_url = getattr(client, "base_url", None)
    if base_url is not None:
        return store_type, str(base_url)
    # Fallback patterns used by custom stores.
    for attr in ("base_url", "_base_url", "url", "_url"):
        value = getattr(store, attr, None)
        if value:
            return store_type, str(value)
    return store_type, None


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

    state_store = _load_project_state_store(project_root)
    if state_store is None:
        print("  State: in-memory store (no project state_store loaded)")
    else:
        store_type, store_url = _describe_state_store(state_store)
        print(f"  State: project store ({store_type})")
        if store_url:
            print(f"  State URL: {store_url}")

    try:
        app = create_playground_app(project_root=project_root, state_store=state_store)
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
