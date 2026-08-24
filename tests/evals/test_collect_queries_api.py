from __future__ import annotations

import json
from pathlib import Path

import pytest

from penguiflow.evals.api import TraceSelector, collect_traces, export_dataset
from penguiflow.state import InMemoryStateStore


@pytest.mark.asyncio
async def test_collect_traces_supports_orchestrator_and_tags(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    package_dir = project_root / "src" / "demo_eval"
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
        "        suffix = query.split()[-1]\n"
        "        return {\n"
        "            'answer': f'answer:{query}',\n"
        "            'metadata': {\n"
        "                'trace_id': f'trace-{suffix}',\n"
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
        "        }\n",
        encoding="utf-8",
    )

    store = InMemoryStateStore()
    result = await collect_traces(
        project_root=project_root,
        state_store=store,
        session_id="session-demo",
        queries=[
            {"query": "hello one", "tags": ["dataset:v1"], "split": "val"},
            {"query": "hello two", "tags": ["dataset:v1"], "split": "test"},
        ],
    )

    assert result["trace_count"] == 2
    refs = await store.list_trace_refs()
    assert len(refs) == 2

    exported = await export_dataset(
        state_store=store,
        output_dir=tmp_path / "bundle",
        selector=TraceSelector(include_tags=("dataset:v1", "split:val")),
    )

    dataset_rows = [
        json.loads(line)
        for line in Path(exported["dataset_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(dataset_rows) == 1
    assert dataset_rows[0]["split"] == "val"
