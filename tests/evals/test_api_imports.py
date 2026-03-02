from __future__ import annotations

import json
from pathlib import Path

import pytest

from penguiflow.evals.api import (
    EvalCollectSpec,
    EvalRunSpec,
    ensure_project_on_sys_path,
    load_candidates,
    load_eval_collect_spec,
    load_eval_run_spec,
    load_eval_spec,
    resolve_callable,
    run_eval_from_spec_file,
    run_eval_from_specs,
    wrap_metric,
)


def test_ensure_project_on_sys_path_prefers_src(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "proj"
    src_dir = project_root / "src"
    package_dir = src_dir / "demo_pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.path", [])

    ensure_project_on_sys_path(project_root)
    ensure_project_on_sys_path(project_root)

    assert Path(__import__("sys").path[0]) == src_dir
    assert __import__("sys").path.count(str(src_dir)) == 1


def test_resolve_callable_loads_function_from_project_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "proj"
    package_dir = project_root / "demo_pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "hooks.py").write_text(
        "def metric(gold, pred):\n    return 1.0 if pred == gold.get('answer') else 0.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.path", [])
    ensure_project_on_sys_path(project_root)

    fn = resolve_callable("demo_pkg.hooks:metric")

    assert callable(fn)
    assert fn({"answer": "A"}, "A") == 1.0


def test_resolve_callable_rejects_invalid_spec() -> None:
    with pytest.raises(ValueError, match="module:callable"):
        resolve_callable("bad-spec")


def test_wrap_metric_supports_minimal_signatures() -> None:
    seen: dict[str, object] = {}

    def metric(gold, pred, pred_trace=None):
        seen["gold"] = gold
        seen["pred"] = pred
        seen["pred_trace"] = pred_trace
        return {"score": 1.0, "feedback": "ok"}

    wrapped = wrap_metric(metric)
    result = wrapped({"answer": "A"}, "A", {"gold": True}, "baseline", {"steps": []})

    assert isinstance(result, dict)
    assert result["score"] == 1.0
    assert seen["pred_trace"] == {"steps": []}


def test_resolve_callable_loads_enterprise_example_metric() -> None:
    project_root = Path(__file__).resolve().parents[2]
    ensure_project_on_sys_path(project_root)

    metric = resolve_callable("examples.planner_enterprise_agent_v2.evals.metrics:policy_metric")

    assert callable(metric)


@pytest.mark.asyncio
async def test_run_eval_from_specs_loads_project_hooks(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "proj"
    package_dir = project_root / "demo_pkg_specs"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "hooks.py").write_text(
        "from penguiflow.planner import Trajectory\n"
        "from penguiflow.state.in_memory import InMemoryStateStore\n"
        "from penguiflow.state.models import StoredEvent\n"
        "\n"
        "_store = None\n"
        "\n"
        "async def state_store_factory():\n"
        "    global _store\n"
        "    if _store is not None:\n"
        "        return _store\n"
        "    store = InMemoryStateStore()\n"
        "    await store.save_event(StoredEvent(trace_id='trace-val', ts=1.0, kind='node_succeeded', "
        "node_name='triage_query', node_id='triage_query', payload={'ok': True}))\n"
        "    await store.save_event(StoredEvent(trace_id='trace-test', ts=2.0, kind='node_succeeded', "
        "node_name='triage_query', node_id='triage_query', payload={'ok': True}))\n"
        "    await store.save_trajectory('trace-val', 'session-1', Trajectory(query='Question val', "
        "llm_context={'tenant_id': 'tenant-a'}, "
        "tool_context={'request_id': 'r-1'}, metadata={'tags': ['split:val', 'dataset:eval']}))\n"
        "    await store.save_trajectory('trace-test', 'session-1', Trajectory(query='Question test', "
        "llm_context={'tenant_id': 'tenant-a'}, "
        "tool_context={'request_id': 'r-2'}, metadata={'tags': ['split:test', 'dataset:eval']}))\n"
        "    _store = store\n"
        "    return store\n"
        "\n"
        "async def run_one(gold, patch_bundle=None):\n"
        "    prompt = None\n"
        "    if isinstance(patch_bundle, dict):\n"
        "        patches = patch_bundle.get('patches', {})\n"
        "        if isinstance(patches, dict):\n"
        "            prompt = patches.get('planner.system_prompt_extra')\n"
        "    if prompt == 'good':\n"
        "        return str(gold.get('answer', ''))\n"
        "    return 'wrong'\n"
        "\n"
        "def metric(gold, pred):\n"
        "    return 1.0 if isinstance(gold, dict) and pred == gold.get('answer') else 0.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.path", [])

    query_suite_path = tmp_path / "query_suite.json"
    query_suite_path.write_text(
        '{"queries": ['
        '{"query_id": "q-val", "text": "Question val", "answer": "A", "split": "val"},'
        '{"query_id": "q-test", "text": "Question test", "answer": "B", "split": "test"}'
        "]}",
        encoding="utf-8",
    )
    trace_ids_path = tmp_path / "trace_ids.txt"
    trace_ids_path.write_text("trace-val\ntrace-test\n", encoding="utf-8")

    result = await run_eval_from_specs(
        project_root=project_root,
        state_store_spec="demo_pkg_specs.hooks:state_store_factory",
        run_one_spec="demo_pkg_specs.hooks:run_one",
        metric_spec="demo_pkg_specs.hooks:metric",
        query_suite_path=query_suite_path,
        trace_ids_path=trace_ids_path,
        output_dir=tmp_path,
        candidates=[
            {"id": "bad", "patches": {"planner.system_prompt_extra": "bad"}},
            {"id": "good", "patches": {"planner.system_prompt_extra": "good"}},
        ],
        session_id="session-1",
    )

    assert result["winner_id"] == "good"


def test_load_candidates_validates_rows(tmp_path) -> None:
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps(
            [
                {"id": "c1", "patches": {"planner.system_prompt_extra": "Use route discipline"}},
                {"id": "c2", "patches": {"planner.system_prompt_extra": "Prefer concise answers"}},
            ]
        ),
        encoding="utf-8",
    )

    rows = load_candidates(path)

    assert len(rows) == 2


def test_load_candidates_accepts_empty_list(tmp_path) -> None:
    path = tmp_path / "candidates.json"
    path.write_text("[]", encoding="utf-8")

    rows = load_candidates(path)

    assert rows == []


def test_load_candidates_rejects_missing_id(tmp_path) -> None:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps([{"patches": {}}]), encoding="utf-8")

    with pytest.raises(ValueError, match="id"):
        load_candidates(path)


def test_load_candidates_rejects_empty_patches(tmp_path) -> None:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps([{"id": "baseline", "patches": {}}]), encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty patches"):
        load_candidates(path)


def test_load_candidates_rejects_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps(
            [
                {"id": "c1", "patches": {"planner.system_prompt_extra": "A"}},
                {"id": "c1", "patches": {"planner.system_prompt_extra": "B"}},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate candidate id"):
        load_candidates(path)


def test_load_candidates_rejects_duplicate_patch_sets(tmp_path) -> None:
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps(
            [
                {"id": "c1", "patches": {"planner.system_prompt_extra": "A"}},
                {"id": "c2", "patches": {"planner.system_prompt_extra": "A"}},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate patch set"):
        load_candidates(path)


def test_load_eval_spec_resolves_relative_paths(tmp_path) -> None:
    spec_path = tmp_path / "eval.spec.json"
    (tmp_path / "datasets").mkdir()
    spec_path.write_text(
        json.dumps(
            {
                "project_root": ".",
                "state_store_spec": "demo_pkg.hooks:state_store_factory",
                "run_one_spec": "demo_pkg.hooks:run_one",
                "metric_spec": "demo_pkg.hooks:metric",
                "query_suite_path": "datasets/query_suite.json",
                "trace_ids_path": "datasets/trace_ids.txt",
                "candidates_path": "datasets/candidates.json",
                "output_dir": "artifacts/eval/run-001",
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_spec(spec_path)

    assert spec.project_root == tmp_path
    assert spec.query_suite_path == tmp_path / "datasets/query_suite.json"
    assert spec.output_dir == tmp_path / "artifacts/eval/run-001"


def test_load_eval_run_spec_resolves_env_files(tmp_path) -> None:
    spec_path = tmp_path / "eval.run.json"
    (tmp_path / "env").mkdir()
    spec_path.write_text(
        json.dumps(
            {
                "project_root": ".",
                "query_suite_path": "datasets/query_suite.json",
                "candidates_path": "datasets/candidates.json",
                "metric_spec": "demo_pkg.hooks:metric",
                "output_dir": "artifacts/eval/run-001",
                "session_id": "session-a",
                "dataset_tag": "dataset:demo",
                "env_files": ["env/local.env", "env/secrets.env"],
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_run_spec(spec_path)

    assert isinstance(spec, EvalRunSpec)
    assert spec.project_root == tmp_path
    assert spec.env_files == (
        tmp_path / "env/local.env",
        tmp_path / "env/secrets.env",
    )


def test_load_eval_collect_spec_resolves_env_files(tmp_path) -> None:
    spec_path = tmp_path / "collect.spec.json"
    (tmp_path / "env").mkdir()
    spec_path.write_text(
        json.dumps(
            {
                "project_root": ".",
                "query_suite_path": "datasets/query_suite.json",
                "output_dir": "artifacts/eval/collect-001",
                "session_id": "session-a",
                "dataset_tag": "dataset:demo",
                "env_files": ["env/local.env", "env/secrets.env"],
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_collect_spec(spec_path)

    assert isinstance(spec, EvalCollectSpec)
    assert spec.project_root == tmp_path
    assert spec.env_files == (
        tmp_path / "env/local.env",
        tmp_path / "env/secrets.env",
    )


@pytest.mark.asyncio
async def test_run_eval_from_spec_file_runs_pipeline(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "proj"
    package_dir = project_root / "demo_pkg_specfile"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "hooks.py").write_text(
        "from penguiflow.planner import Trajectory\n"
        "from penguiflow.state.in_memory import InMemoryStateStore\n"
        "from penguiflow.state.models import StoredEvent\n"
        "\n"
        "_store = None\n"
        "\n"
        "async def state_store_factory():\n"
        "    global _store\n"
        "    if _store is not None:\n"
        "        return _store\n"
        "    store = InMemoryStateStore()\n"
        "    await store.save_event(StoredEvent(trace_id='trace-val', ts=1.0, kind='node_succeeded', "
        "node_name='triage_query', node_id='triage_query', payload={'ok': True}))\n"
        "    await store.save_event(StoredEvent(trace_id='trace-test', ts=2.0, kind='node_succeeded', "
        "node_name='triage_query', node_id='triage_query', payload={'ok': True}))\n"
        "    await store.save_trajectory('trace-val', 'session-1', Trajectory(query='Question val', "
        "llm_context={'tenant_id': 'tenant-a'}, "
        "tool_context={'request_id': 'r-1'}, metadata={'tags': ['split:val', 'dataset:eval']}))\n"
        "    await store.save_trajectory('trace-test', 'session-1', Trajectory(query='Question test', "
        "llm_context={'tenant_id': 'tenant-a'}, "
        "tool_context={'request_id': 'r-2'}, metadata={'tags': ['split:test', 'dataset:eval']}))\n"
        "    _store = store\n"
        "    return store\n"
        "\n"
        "async def run_one(gold, patch_bundle=None):\n"
        "    prompt = None\n"
        "    if isinstance(patch_bundle, dict):\n"
        "        patches = patch_bundle.get('patches', {})\n"
        "        if isinstance(patches, dict):\n"
        "            prompt = patches.get('planner.system_prompt_extra')\n"
        "    if prompt == 'good':\n"
        "        return str(gold.get('answer', ''))\n"
        "    return 'wrong'\n"
        "\n"
        "def metric(gold, pred):\n"
        "    return 1.0 if isinstance(gold, dict) and pred == gold.get('answer') else 0.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.path", [])

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir(parents=True)
    (inputs_dir / "query_suite.json").write_text(
        '{"queries": ['
        '{"query_id": "q-val", "text": "Question val", "answer": "A", "split": "val"},'
        '{"query_id": "q-test", "text": "Question test", "answer": "B", "split": "test"}'
        "]}",
        encoding="utf-8",
    )
    (inputs_dir / "trace_ids.txt").write_text("trace-val\ntrace-test\n", encoding="utf-8")
    (inputs_dir / "candidates.json").write_text(
        json.dumps(
            [
                {"id": "bad", "patches": {"planner.system_prompt_extra": "bad"}},
                {"id": "good", "patches": {"planner.system_prompt_extra": "good"}},
            ]
        ),
        encoding="utf-8",
    )
    spec_path = tmp_path / "eval.spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "project_root": str(project_root),
                "state_store_spec": "demo_pkg_specfile.hooks:state_store_factory",
                "run_one_spec": "demo_pkg_specfile.hooks:run_one",
                "metric_spec": "demo_pkg_specfile.hooks:metric",
                "query_suite_path": str(inputs_dir / "query_suite.json"),
                "trace_ids_path": str(inputs_dir / "trace_ids.txt"),
                "candidates_path": str(inputs_dir / "candidates.json"),
                "output_dir": str(tmp_path / "artifacts"),
                "session_id": "session-1",
            }
        ),
        encoding="utf-8",
    )

    result = await run_eval_from_spec_file(spec_path)

    assert result["winner_id"] == "good"
