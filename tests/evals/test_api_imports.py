from __future__ import annotations

import json
from pathlib import Path

import pytest

import penguiflow.evals.api as eval_api
from penguiflow.evals.api import (
    EvalCollectSpec,
    EvalDatasetSpec,
    MetricDefinition,
    QueryCase,
    TraceSelector,
    describe_metric,
    ensure_project_on_sys_path,
    evaluate_dataset_from_spec_file,
    load_candidates,
    load_eval_collect_spec,
    load_eval_dataset_spec,
    metric,
    resolve_callable,
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


def test_ensure_project_on_sys_path_uses_project_root_for_package_dir(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "demo_pkg"
    project_root.mkdir(parents=True)
    (project_root / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.path", [])

    ensure_project_on_sys_path(project_root)

    assert Path(__import__("sys").path[0]) == project_root


def test_candidate_packages_does_not_include_project_root_package_name(tmp_path) -> None:
    project_root = tmp_path / "demo_pkg"
    project_root.mkdir(parents=True)
    (project_root / "__init__.py").write_text("", encoding="utf-8")

    packages = eval_api._candidate_packages(project_root)

    assert packages == []


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


def test_evals_api_does_not_import_cli_modules() -> None:
    api_path = Path(__file__).resolve().parents[2] / "penguiflow" / "evals" / "api.py"
    source = api_path.read_text(encoding="utf-8")

    assert "from penguiflow.cli." not in source


def test_eval_api_dataclasses_use_slots() -> None:
    for cls in (EvalDatasetSpec, EvalCollectSpec, QueryCase, TraceSelector):
        assert getattr(cls, "__slots__", None) is not None


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


def test_metric_decorator_uses_docstring_and_declared_criteria() -> None:
    @metric(
        name="Policy Compliance",
        criteria=[
            {"id": "starts_with_triage", "label": "Starts with triage"},
            {"id": "uses_expected_terminal_tool", "label": "Uses expected terminal tool"},
        ],
    )
    def policy_metric(gold, pred):
        """Validate route discipline for policy queries."""

        del gold, pred
        return {"score": 1.0}

    description = describe_metric(policy_metric)

    assert isinstance(description, MetricDefinition)
    assert description.name == "Policy Compliance"
    assert description.summary == "Validate route discipline for policy queries."
    assert [item.id for item in description.criteria] == [
        "starts_with_triage",
        "uses_expected_terminal_tool",
    ]


def test_metric_decorator_rejects_duplicate_criterion_ids() -> None:
    with pytest.raises(ValueError, match="duplicate criterion id"):

        @metric(
            name="Policy Compliance",
            criteria=[
                {"id": "starts_with_triage", "label": "Starts with triage"},
                {"id": "starts_with_triage", "label": "Starts with triage again"},
            ],
        )
        def policy_metric(gold, pred):
            del gold, pred
            return {"score": 1.0}


def test_enterprise_example_metric_exposes_definition_and_check_ids() -> None:
    metric_fn = resolve_callable("examples.planner_enterprise_agent_v2.evals.metrics:policy_metric")
    definition = describe_metric(metric_fn)

    assert definition.name == "Policy Compliance"
    assert [criterion.id for criterion in definition.criteria] == [
        "starts_with_triage",
        "uses_expected_terminal_tool",
        "stays_within_tool_budget",
    ]

    result = metric_fn(
        {"question": "how are you"},
        "irrelevant",
        None,
        "baseline",
        {
            "steps": [
                {"action": {"next_node": "triage_query"}},
                {"action": {"next_node": "answer_general"}},
            ]
        },
    )
    assert isinstance(result, dict)
    assert result.get("checks") == {
        "starts_with_triage": True,
        "uses_expected_terminal_tool": True,
        "stays_within_tool_budget": True,
    }


def test_enterprise_fail_metric_demo_exposes_definition_and_check_ids() -> None:
    metric_fn = resolve_callable("examples.planner_enterprise_agent_v2.evals.metrics:fail_metric_demo")
    definition = describe_metric(metric_fn)

    assert definition.name == "Failure Demo"
    assert [criterion.id for criterion in definition.criteria] == [
        "starts_with_triage",
        "uses_expected_terminal_tool",
        "stays_within_demo_budget",
    ]


def test_enterprise_fail_metric_demo_flags_demo_budget_failure() -> None:
    metric_fn = resolve_callable("examples.planner_enterprise_agent_v2.evals.metrics:fail_metric_demo")

    result = metric_fn(
        {"question": "analyze this document report"},
        "irrelevant",
        None,
        "baseline",
        {
            "steps": [
                {"action": {"next_node": "triage_query"}},
                {"action": {"next_node": "parse_documents"}},
                {"action": {"next_node": "extract_metadata"}},
                {"action": {"next_node": "summarize_documents"}},
                {"action": {"next_node": "analyze_documents"}},
            ]
        },
    )

    assert isinstance(result, dict)
    checks = result.get("checks")
    assert isinstance(checks, dict)
    assert checks.get("starts_with_triage") is True
    assert checks.get("uses_expected_terminal_tool") is True
    assert checks.get("stays_within_demo_budget") is False
    feedback = result.get("feedback")
    assert isinstance(feedback, str)
    assert "demo budget allows" in feedback
    assert "route=" not in feedback
    assert "tools=" not in feedback


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


def test_load_eval_collect_spec_rejects_env_files_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
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

    with pytest.raises(ValueError, match="env_files.*no longer supported"):
        load_eval_collect_spec(spec_path)


def test_load_eval_collect_spec_resolves_relative_fields_from_project_root(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    project_root = tmp_path / "project"
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "collect.spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "project_root": "project",
                "query_suite_path": "datasets/query_suite.json",
                "output_dir": "artifacts/eval/collect",
                "session_id": "session-collect",
                "dataset_tag": "dataset:demo",
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_collect_spec(spec_path)

    assert isinstance(spec, EvalCollectSpec)
    assert spec.project_root == project_root
    assert spec.query_suite_path == project_root / "datasets/query_suite.json"
    assert spec.output_dir == project_root / "artifacts/eval/collect"


def test_load_eval_dataset_spec_resolves_relative_fields_from_project_root_when_provided(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    project_root = tmp_path / "project"
    spec_dir = tmp_path / "specs" / "nested"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "evaluate.spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "project_root": "project",
                "dataset_path": "bundle/dataset.jsonl",
                "candidates_path": "datasets/candidates.json",
                "metric_spec": "demo.metric:metric",
                "output_dir": "artifacts/eval/rerun",
                "min_test_score": 0.8,
                "report_path": "reports/eval-dataset.json",
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_dataset_spec(spec_path)

    assert isinstance(spec, EvalDatasetSpec)
    assert spec.project_root == project_root
    assert spec.dataset_path == project_root / "bundle/dataset.jsonl"
    assert spec.candidates_path == project_root / "datasets/candidates.json"
    assert spec.min_test_score == 0.8
    assert spec.output_dir == project_root / "artifacts/eval/rerun"
    assert spec.report_path == project_root / "reports/eval-dataset.json"


def test_load_eval_collect_spec_resolves_project_root_from_cwd(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec_dir = tmp_path / "evals" / "native_avails_v1"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "collect.spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "project_root": ".",
                "query_suite_path": "evals/native_avails_v1/query_suite.json",
                "output_dir": "artifacts/eval/native_avails_v1/collect-local",
                "session_id": "session-collect",
                "dataset_tag": "dataset:demo",
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_collect_spec(spec_path)

    assert spec.project_root == tmp_path
    assert spec.query_suite_path == tmp_path / "evals/native_avails_v1/query_suite.json"


def test_load_eval_dataset_spec_resolves_project_root_from_cwd(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec_dir = tmp_path / "evals" / "native_avails_v1"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "evaluate.spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "project_root": ".",
                "dataset_path": "artifacts/eval/native_avails_v1/run-local/bundle/dataset.jsonl",
                "candidates_path": "evals/native_avails_v1/candidates.json",
                "metric_spec": "demo.metric:metric",
                "output_dir": "artifacts/eval/native_avails_v1/rerun",
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_dataset_spec(spec_path)

    assert spec.project_root == tmp_path
    assert spec.dataset_path == tmp_path / "artifacts/eval/native_avails_v1/run-local/bundle/dataset.jsonl"


def test_load_eval_dataset_spec_rejects_env_files_key(tmp_path) -> None:
    spec_path = tmp_path / "evaluate.spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "dataset_path": "dataset.jsonl",
                "metric_spec": "demo.metric:metric",
                "output_dir": "artifacts/eval/rerun",
                "env_files": [".env"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="env_files.*no longer supported"):
        load_eval_dataset_spec(spec_path)


def test_load_eval_dataset_spec_allows_missing_candidates_path(tmp_path) -> None:
    spec_path = tmp_path / "evaluate.spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "dataset_path": "dataset.jsonl",
                "metric_spec": "demo.metric:metric",
                "output_dir": "artifacts/eval/rerun",
            }
        ),
        encoding="utf-8",
    )

    spec = load_eval_dataset_spec(spec_path)

    assert spec.candidates_path is None


@pytest.mark.asyncio
async def test_evaluate_dataset_from_spec_file_runs_pipeline(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    (inputs_dir / "dataset.jsonl").write_text(
        json.dumps(
            {
                "example_id": "q-val",
                "split": "val",
                "question": "Question val",
                "answer": "A",
                "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
            }
        )
        + "\n"
        + json.dumps(
            {
                "example_id": "q-test",
                "split": "test",
                "question": "Question test",
                "answer": "B",
                "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (inputs_dir / "candidates.json").write_text(
        json.dumps(
            [
                {"id": "bad", "patches": {"planner.system_prompt_extra": "bad"}},
                {"id": "good", "patches": {"planner.system_prompt_extra": "good"}},
            ]
        ),
        encoding="utf-8",
    )
    spec_path = tmp_path / "evaluate.spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "project_root": str(project_root),
                "run_one_spec": "demo_pkg_specfile.hooks:run_one",
                "metric_spec": "demo_pkg_specfile.hooks:metric",
                "dataset_path": str(inputs_dir / "dataset.jsonl"),
                "candidates_path": str(inputs_dir / "candidates.json"),
                "output_dir": str(tmp_path / "artifacts"),
            }
        ),
        encoding="utf-8",
    )

    result = await evaluate_dataset_from_spec_file(spec_path)

    assert result["mode"] == "candidates"
    assert result["winner_id"] == "good"


@pytest.mark.asyncio
async def test_evaluate_dataset_from_spec_file_runs_baseline_without_candidates(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "proj"
    package_dir = project_root / "demo_pkg_baseline"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "hooks.py").write_text(
        "async def run_one(gold, patch_bundle=None):\n"
        "    del patch_bundle\n"
        "    return str(gold.get('answer', ''))\n"
        "\n"
        "def metric(gold, pred):\n"
        "    return 1.0 if isinstance(gold, dict) and pred == gold.get('answer') else 0.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.path", [])

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir(parents=True)
    (inputs_dir / "dataset.jsonl").write_text(
        json.dumps(
            {
                "example_id": "q-val",
                "split": "val",
                "question": "Question val",
                "answer": "A",
                "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
            }
        )
        + "\n"
        + json.dumps(
            {
                "example_id": "q-test",
                "split": "test",
                "question": "Question test",
                "answer": "B",
                "gold_trace": {"inputs": {"llm_context": {}, "tool_context": {}}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "evaluate.spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "project_root": str(project_root),
                "run_one_spec": "demo_pkg_baseline.hooks:run_one",
                "metric_spec": "demo_pkg_baseline.hooks:metric",
                "dataset_path": str(inputs_dir / "dataset.jsonl"),
                "output_dir": str(tmp_path / "artifacts"),
            }
        ),
        encoding="utf-8",
    )

    result = await evaluate_dataset_from_spec_file(spec_path)

    assert result["mode"] == "baseline"
    assert "winner_id" not in result
