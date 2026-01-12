from __future__ import annotations

import json

import pytest

from penguiflow.planner.migration import normalize_action, try_normalize_action


def test_normalize_action_legacy_tool_call_roundtrip() -> None:
    raw = json.dumps(
        {
            "thought": "search",
            "next_node": "search_web",
            "args": {"query": "penguins"},
            "plan": None,
            "join": None,
        }
    )

    action = normalize_action(raw)
    assert action.next_node == "search_web"
    assert action.args == {"query": "penguins"}
    assert action.thought == "search"


def test_normalize_action_unified_final_response_is_terminal() -> None:
    raw = json.dumps({"next_node": "final_response", "args": {"answer": "hi"}})
    action = normalize_action(raw)
    assert action.next_node == "final_response"
    assert action.args["answer"] == "hi"
    assert action.args["raw_answer"] == "hi"


def test_normalize_action_unified_parallel_preserves_steps_and_join() -> None:
    raw = json.dumps(
        {
            "next_node": "parallel",
            "args": {
                "steps": [{"node": "tool_a", "args": {"x": 1}}, {"node": "tool_b", "args": {}}],
                "join": {"node": "combine", "args": {}, "inject": {"results": "$all"}},
            },
        }
    )
    action = normalize_action(raw)
    assert action.next_node == "parallel"
    assert [step["node"] for step in action.args["steps"]] == ["tool_a", "tool_b"]
    assert action.args["join"]["node"] == "combine"
    assert action.args["join"]["inject"]["results"] == "$all"


def test_normalize_action_unified_plan_alias_is_accepted() -> None:
    raw = json.dumps({"next_node": "plan", "args": {"steps": [{"node": "tool_a", "args": {}}]}})
    action = normalize_action(raw)
    assert action.next_node == "parallel"
    assert action.args["steps"][0]["node"] == "tool_a"


def test_normalize_action_task_subagent_maps_to_tasks_spawn() -> None:
    raw = json.dumps(
        {
            "next_node": "task.subagent",
            "args": {
                "name": "Research",
                "query": "do the thing",
                "merge_strategy": "HUMAN_GATED",
                "group": "g1",
            },
        }
    )
    action = normalize_action(raw)
    assert action.next_node == "tasks.spawn"
    assert isinstance(action.args, dict)
    assert action.args["mode"] == "subagent"
    assert action.args["query"] == "do the thing"
    assert action.args["merge_strategy"] == "human_gated"
    assert action.args["group"] == "g1"


def test_normalize_action_task_tool_maps_to_tasks_spawn_job() -> None:
    raw = json.dumps(
        {
            "next_node": "task.tool",
            "args": {
                "name": "Job",
                "tool": "fetch",
                "tool_args": {"url": "https://example.com"},
                "merge_strategy": "append",
            },
        }
    )
    action = normalize_action(raw)
    assert action.next_node == "tasks.spawn"
    assert isinstance(action.args, dict)
    assert action.args["mode"] == "job"
    assert action.args["tool_name"] == "fetch"
    assert action.args["tool_args"] == {"url": "https://example.com"}
    assert action.args["merge_strategy"] == "append"


def test_try_normalize_action_returns_none_on_garbage() -> None:
    assert try_normalize_action("not json") is None


def test_normalize_action_raises_on_garbage() -> None:
    with pytest.raises(ValueError):
        normalize_action("not json")
