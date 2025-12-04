from __future__ import annotations

from pathlib import Path
from unittest import mock

from click.testing import CliRunner

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

    # ensure dist exists so guard passes
    dist_dir = Path(__file__).parents[2] / "penguiflow" / "cli" / "playground_ui" / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")

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
