from __future__ import annotations

import json
from pathlib import Path

import pytest

from penguiflow.evals.api import collect_and_export_traces


@pytest.mark.asyncio
async def test_collect_and_export_traces_runs_collection_and_exports_traces(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    package_dir = project_root / "src" / "demo_eval_collect"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "config.py").write_text(
        "class Config:\n    @classmethod\n    def from_env(cls):\n        return cls()\n",
        encoding="utf-8",
    )
    (package_dir / "orchestrator.py").write_text(
        "class DemoOrchestrator:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n"
        "\n"
        "    async def execute(self, query: str, *, tenant_id: str = 'default'):\n"
        "        return {\n"
        "            'answer': f'answer:{query}',\n"
        "            'metadata': {\n"
        "                'trace_id': f\"trace-{query.replace(' ', '-')}\",\n"
        "                'planner': {\n"
        "                    'steps': [],\n"
        "                    'llm_context': {'tenant_id': tenant_id},\n"
        "                    'tool_context': {'tenant_id': tenant_id},\n"
        "                    'trajectory_metadata': {'tags': []},\n"
        "                },\n"
        "            },\n"
        "        }\n",
        encoding="utf-8",
    )

    query_suite_path = tmp_path / "query_suite.json"
    query_suite_path.write_text(
        json.dumps(
            {
                "queries": [
                    {"query_id": "q1", "text": "hello", "answer": "answer:hello", "split": "val"},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = await collect_and_export_traces(
        project_root=project_root,
        query_suite_path=query_suite_path,
        output_dir=tmp_path / "out",
        session_id="session-collect",
        dataset_tag="dataset:demo-collect",
        agent_package="demo_eval_collect",
    )

    assert result["trace_count"] == 1
    assert Path(result["dataset_path"]).exists()
    assert Path(result["manifest_path"]).exists()
    assert "trace_ids_path" not in result
    assert "trace_path" not in result
