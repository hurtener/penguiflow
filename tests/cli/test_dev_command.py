from __future__ import annotations

from pathlib import Path
from unittest import mock

from click.testing import CliRunner
from fastapi import FastAPI

from penguiflow.cli import app
from penguiflow.cli.dev import run_dev


def test_run_dev_invokes_uvicorn_and_browser(monkeypatch, tmp_path: Path) -> None:
    called = {}

    class FakeServer:
        def __init__(self, config) -> None:
            called["config"] = config

        def run(self) -> None:
            called["run_called"] = True

    monkeypatch.setattr("uvicorn.Server", FakeServer)
    browser = mock.Mock()
    monkeypatch.setattr("webbrowser.open_new", browser)

    # Mock the UI assets check to avoid depending on real dist directory
    monkeypatch.setattr("penguiflow.cli.dev._ensure_ui_assets", lambda _: None)

    pkg_dir = tmp_path / "src" / "demo_agent"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "orchestrator.py").write_text(
        "class DemoAgentOrchestrator:\n"
        "    def __init__(self, config=None):\n"
        "        self._planner = None\n"
        "    async def execute(self, query, tenant_id, user_id, session_id):\n"
        "        return type('R', (), {'answer': query, 'trace_id': 't-1', 'metadata': {}})()\n",
        encoding="utf-8",
    )

    result = run_dev(project_root=tmp_path, host="0.0.0.0", port=9000, open_browser=True)
    assert called["config"].host == "0.0.0.0"
    assert called["config"].port == 9000
    assert result.url == "http://0.0.0.0:9000"
    browser.assert_called_once()


def test_dev_cli_invokes_run_dev(monkeypatch, tmp_path: Path) -> None:
    called = {}

    def fake_run_dev(project_root, host, port, open_browser):
        called["project_root"] = project_root
        called["host"] = host
        called["port"] = port
        called["open_browser"] = open_browser

    monkeypatch.setattr("penguiflow.cli.main.run_dev", fake_run_dev, raising=False)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "dev",
            "--project-root",
            str(tmp_path),
            "--host",
            "0.0.0.0",
            "--port",
            "8100",
            "--no-browser",
        ],
    )
    assert result.exit_code == 0
    assert called["project_root"] == tmp_path
    assert called["host"] == "0.0.0.0"
    assert called["port"] == 8100
    assert called["open_browser"] is False


def test_run_dev_passes_discovered_state_store(monkeypatch, tmp_path: Path) -> None:
    called = {}

    class FakeServer:
        def __init__(self, config) -> None:
            called["config"] = config

        def run(self) -> None:
            called["run_called"] = True

    def fake_create_playground_app(*, project_root, state_store=None):
        called["project_root"] = project_root
        called["state_store"] = state_store
        return FastAPI()

    monkeypatch.setattr("uvicorn.Server", FakeServer)
    monkeypatch.setattr("penguiflow.cli.dev._ensure_ui_assets", lambda _: None)
    monkeypatch.setattr("penguiflow.cli.dev._load_project_state_store", lambda _: {"kind": "store"})
    monkeypatch.setattr("penguiflow.cli.dev.create_playground_app", fake_create_playground_app)

    run_dev(project_root=tmp_path, host="127.0.0.1", port=9100, open_browser=False)

    assert called["project_root"] == tmp_path
    assert called["state_store"] == {"kind": "store"}
