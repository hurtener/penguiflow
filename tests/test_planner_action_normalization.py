from __future__ import annotations

import json

import pytest

from penguiflow.planner.migration import (
    dump_action_legacy,
    normalize_action,
    normalize_action_with_debug,
    try_normalize_action,
)
from penguiflow.planner.models import PlannerAction


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


def test_normalize_action_final_response_args_as_string() -> None:
    """Test weak model pattern where args is a string instead of {"answer": ...}."""
    raw = json.dumps({"next_node": "final_response", "args": "Here is my answer to your question"})
    action = normalize_action(raw)
    assert action.next_node == "final_response"
    assert action.args["answer"] == "Here is my answer to your question"
    assert action.args["raw_answer"] == "Here is my answer to your question"


def test_normalize_action_final_response_args_as_list_of_strings() -> None:
    """Test weak model pattern where args is a list of strings instead of {"answer": ...}."""
    raw = json.dumps({"next_node": "final_response", "args": ["Here is my answer.", "Second line."]})
    action = normalize_action(raw)
    assert action.next_node == "final_response"
    assert action.args["answer"] == "Here is my answer.\nSecond line."
    assert action.args["raw_answer"] == "Here is my answer.\nSecond line."


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


def test_normalize_action_parses_first_json_object_with_trailing_text() -> None:
    raw = (
        '{"next_node":"data_source_info","args":{"query":"x"}}'
        "\n\nHere is my thinking (ignore this). {not json}\n"
    )
    action = normalize_action(raw)
    assert action.next_node == "data_source_info"
    assert action.args["query"] == "x"


def test_normalize_action_parses_from_json_code_fence() -> None:
    raw = (
        "Some preface.\n"
        "```json\n"
        '{"next_node":"data_source_info","args":{"query":"x"}}\n'
        "```\n"
        "Some suffix."
    )
    action = normalize_action(raw)
    assert action.next_node == "data_source_info"
    assert action.args["query"] == "x"


def test_normalize_action_prefers_action_dict_when_multiple_json_objects() -> None:
    raw = (
        '{"next_node":"data_source_info","args":{"query":"x"}}\n'
        '{"answer":"this is not an action"}\n'
    )
    action = normalize_action(raw)
    assert action.next_node == "data_source_info"
    assert action.args["query"] == "x"


def test_normalize_action_handles_multiple_json_objects_in_fence() -> None:
    raw = (
        "```json\n"
        '{"next_node":"data_source_info","args":{"query":"x"}}\n'
        '{"next_node":"final_response","args":{"answer":"y"}}\n'
        "```\n"
    )
    action = normalize_action(raw)
    assert action.next_node == "data_source_info"
    assert action.args["query"] == "x"


def test_normalize_action_with_debug_reports_ignored_text() -> None:
    raw = (
        "thinking: blah blah\n"
        '{"next_node":"data_source_info","args":{"query":"x"}}\n'
        "extra: more text\n"
    )
    action, debug = normalize_action_with_debug(raw)
    assert action.next_node == "data_source_info"
    assert debug is not None
    assert debug["ignored_text_len"] is not None
    assert isinstance(debug["ignored_text_preview"], str)
    assert "thinking" in debug["ignored_text_preview"] or "extra" in debug["ignored_text_preview"]


# ─── RFC_UNIFIED_ACTION_SCHEMA: dump_action_legacy tests ──────────────────────


def test_dump_action_legacy_tool_call() -> None:
    """dump_action_legacy should render a tool call with legacy fields."""
    action = PlannerAction(next_node="search_web", args={"query": "penguins"}, thought="searching")
    legacy = dump_action_legacy(action)

    assert legacy["thought"] == "searching"
    assert legacy["next_node"] == "search_web"
    assert legacy["args"] == {"query": "penguins"}
    assert legacy["plan"] is None
    assert legacy["join"] is None


def test_dump_action_legacy_final_response() -> None:
    """dump_action_legacy should render final_response with next_node=None."""
    action = PlannerAction(next_node="final_response", args={"answer": "42"}, thought="done")
    legacy = dump_action_legacy(action)

    assert legacy["thought"] == "done"
    assert legacy["next_node"] is None  # Legacy uses null for terminal
    assert legacy["args"]["answer"] == "42"
    assert legacy["args"]["raw_answer"] == "42"  # Legacy format adds raw_answer
    assert legacy["plan"] is None
    assert legacy["join"] is None


def test_dump_action_legacy_parallel() -> None:
    """dump_action_legacy should render parallel as plan/join fields."""
    action = PlannerAction(
        next_node="parallel",
        args={
            "steps": [{"node": "tool_a", "args": {"x": 1}}, {"node": "tool_b", "args": {}}],
            "join": {"node": "combine", "args": {}},
        },
        thought="running parallel",
    )
    legacy = dump_action_legacy(action)

    assert legacy["thought"] == "running parallel"
    assert legacy["next_node"] is None  # Legacy uses null for parallel
    assert legacy["plan"] == [{"node": "tool_a", "args": {"x": 1}}, {"node": "tool_b", "args": {}}]
    assert legacy["join"] == {"node": "combine", "args": {}}
    assert legacy["args"] is None


def test_dump_action_legacy_roundtrip_tool_call() -> None:
    """Tool call should survive dump -> normalize roundtrip."""
    original = PlannerAction(next_node="search_web", args={"query": "test"}, thought="searching")
    legacy = dump_action_legacy(original)
    restored = normalize_action(json.dumps(legacy))

    assert restored.next_node == original.next_node
    assert restored.args == original.args


def test_dump_action_legacy_roundtrip_final_response() -> None:
    """Final response should survive dump -> normalize roundtrip."""
    original = PlannerAction(next_node="final_response", args={"answer": "The answer"}, thought="done")
    legacy = dump_action_legacy(original)
    restored = normalize_action(json.dumps(legacy))

    assert restored.next_node == "final_response"
    assert restored.args["answer"] == "The answer"


def test_dump_action_legacy_roundtrip_parallel() -> None:
    """Parallel plan should survive dump -> normalize roundtrip."""
    original = PlannerAction(
        next_node="parallel",
        args={
            "steps": [{"node": "tool_a", "args": {"x": 1}}],
            "join": {"node": "combine", "args": {}, "inject": {"results": "$results"}},
        },
    )
    legacy = dump_action_legacy(original)
    restored = normalize_action(json.dumps(legacy))

    assert restored.next_node == "parallel"
    assert len(restored.args["steps"]) == 1
    assert restored.args["steps"][0]["node"] == "tool_a"
    assert restored.args["join"]["node"] == "combine"


def test_dump_action_legacy_default_thought() -> None:
    """dump_action_legacy should use default thought when empty."""
    action = PlannerAction(next_node="some_tool", args={})
    legacy = dump_action_legacy(action)

    assert legacy["thought"] == "planning next step"  # Default from migration.py
