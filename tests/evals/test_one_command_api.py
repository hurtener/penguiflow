from __future__ import annotations

import json
from pathlib import Path

import pytest

from penguiflow.evals.api import collect_and_export_traces, run_eval


@pytest.mark.asyncio
async def test_run_eval_uses_discovered_agent_and_inmemory_store(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    package_dir = project_root / "src" / "demo_eval_onecmd"
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
        "        payload = {\n"
        "            'answer': f'answer:{query}',\n"
        "            'metadata': {\n"
        "                'trace_id': f\"trace-{query.replace(' ', '-')}\",\n"
        "                'planner': {\n"
        "                    'steps': [\n"
        "                        {\n"
        "                            'thought': 'route',\n"
        "                            'action': {'next_node': 'triage_query', 'args': {}},\n"
        "                            'observation': {'route': 'general'},\n"
        "                        }\n"
        "                    ],\n"
        "                    'llm_context': {'tenant_id': tenant_id},\n"
        "                    'tool_context': {'tenant_id': tenant_id},\n"
        "                    'trajectory_metadata': {'tags': ['existing:tag']},\n"
        "                },\n"
        "            },\n"
        "        }\n"
        "        return payload\n",
        encoding="utf-8",
    )
    (package_dir / "metric_hooks.py").write_text(
        "def metric(gold, pred):\n    return 1.0 if isinstance(gold, dict) and pred == gold.get('answer') else 0.0\n",
        encoding="utf-8",
    )

    query_suite_path = tmp_path / "query_suite.json"
    query_suite_path.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query_id": "q1",
                        "text": "hello one",
                        "answer": "answer:hello one",
                        "split": "val",
                    },
                    {
                        "query_id": "q2",
                        "text": "hello two",
                        "answer": "answer:hello two",
                        "split": "test",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(
        json.dumps([{"id": "candidate-a", "patches": {"planner.system_prompt_extra": "Use strict routing"}}]),
        encoding="utf-8",
    )

    result = await run_eval(
        project_root=project_root,
        query_suite_path=query_suite_path,
        candidates_path=candidates_path,
        metric_spec="demo_eval_onecmd.metric_hooks:metric",
        output_dir=tmp_path / "out",
        session_id="session-demo",
        dataset_tag="dataset:demo-v1",
    )

    assert result["winner_id"] == "candidate-a"
    assert result["passed"] is True
    assert "trace_ids_path" not in result
    assert "bundle_dataset_path" not in result
    assert "bundle_manifest_path" not in result


@pytest.mark.asyncio
async def test_run_eval_respects_agent_package_selection(tmp_path: Path) -> None:
    project_root = tmp_path / "examples"
    good_pkg = project_root / "src" / "good_pkg"
    bad_pkg = project_root / "src" / "bad_pkg"
    good_pkg.mkdir(parents=True)
    bad_pkg.mkdir(parents=True)
    (good_pkg / "__init__.py").write_text("", encoding="utf-8")
    (bad_pkg / "__init__.py").write_text("", encoding="utf-8")
    (good_pkg / "config.py").write_text(
        "class Config:\n    @classmethod\n    def from_env(cls):\n        return cls()\n",
        encoding="utf-8",
    )
    (bad_pkg / "config.py").write_text(
        "class Config:\n    @classmethod\n    def from_env(cls):\n        return cls()\n",
        encoding="utf-8",
    )
    (good_pkg / "orchestrator.py").write_text(
        "class GoodOrchestrator:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n"
        "\n"
        "    async def execute(self, query: str, *, tenant_id: str = 'default'):\n"
        "        return {'answer': f'good:{query}', 'metadata': {'trace_id': f'good-{query}', 'planner': {'steps': [], "
        "'llm_context': {'tenant_id': tenant_id}, 'tool_context': {'tenant_id': tenant_id}}}}\n",
        encoding="utf-8",
    )
    (bad_pkg / "orchestrator.py").write_text(
        "class BadOrchestrator:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n"
        "\n"
        "    async def execute(self, query: str, *, tenant_id: str = 'default'):\n"
        "        return {'answer': f'bad:{query}', 'metadata': {'trace_id': f'bad-{query}', 'planner': {'steps': [], "
        "'llm_context': {'tenant_id': tenant_id}, 'tool_context': {'tenant_id': tenant_id}}}}\n",
        encoding="utf-8",
    )
    (good_pkg / "metric_hooks.py").write_text(
        "def metric(gold, pred):\n    return 1.0 if isinstance(gold, dict) and pred == gold.get('answer') else 0.0\n",
        encoding="utf-8",
    )

    query_suite_path = tmp_path / "query_suite.json"
    query_suite_path.write_text(
        json.dumps({"queries": [{"query_id": "q1", "text": "hello", "answer": "good:hello", "split": "val"}]}),
        encoding="utf-8",
    )
    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(
        json.dumps([{"id": "candidate-a", "patches": {"planner.system_prompt_extra": "Use strict routing"}}]),
        encoding="utf-8",
    )

    result = await run_eval(
        project_root=project_root,
        query_suite_path=query_suite_path,
        candidates_path=candidates_path,
        metric_spec="good_pkg.metric_hooks:metric",
        output_dir=tmp_path / "out",
        session_id="session-demo",
        dataset_tag="dataset:demo-v1",
        agent_package="good_pkg",
    )

    assert result["winner_id"] == "candidate-a"


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
