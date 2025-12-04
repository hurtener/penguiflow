from __future__ import annotations

import json

from penguiflow.planner import prompts


def test_render_tool_includes_optional_fields() -> None:
    record = {
        "name": "search",
        "desc": "Lookup",
        "side_effects": "read",
        "args_schema": {"title": "Args"},
        "out_schema": {"title": "Out"},
        "tags": ["a", "b"],
        "auth_scopes": ["scope"],
        "cost_hint": "low",
        "latency_hint_ms": 42,
        "safety_notes": "careful",
        "extra": {"foo": "bar"},
    }
    rendered = prompts.render_tool(record)
    assert "tags" in rendered
    assert "auth_scopes" in rendered
    assert "extra" in rendered


def test_build_system_prompt_appends_extra_guidance() -> None:
    prompt = prompts.build_system_prompt([
        {
            "name": "tool",
            "desc": "do",
            "side_effects": "pure",
            "args_schema": {},
            "out_schema": {},
        }
    ], extra="Stay focused.")
    assert "Stay focused." in prompt


def test_build_user_prompt_serialises_context() -> None:
    payload = prompts.build_user_prompt("question", {"tenant": "acme"})
    assert "tenant" in payload


def test_render_helpers() -> None:
    error_obs = prompts.render_observation(observation=None, error="boom")
    error_payload = json.loads(error_obs)
    assert error_payload["error"] == "boom"
    output_error = prompts.render_output_validation_error("ghost", "bad")
    assert "returned data" in output_error
    invalid = prompts.render_invalid_node("ghost", ["known"])
    assert "ghost" in invalid
    repair = prompts.render_repair_message("oops")
    assert "oops" in repair


def test_build_system_prompt_includes_current_date() -> None:
    prompt = prompts.build_system_prompt([], current_date="2025-12-04")
    assert "Current date: 2025-12-04" in prompt


def test_build_system_prompt_default_date() -> None:
    from datetime import date

    prompt = prompts.build_system_prompt([])
    expected_date = date.today().isoformat()
    assert f"Current date: {expected_date}" in prompt


def test_build_system_prompt_has_tagged_sections() -> None:
    prompt = prompts.build_system_prompt([])
    # Check for key tagged sections
    assert "<identity>" in prompt
    assert "</identity>" in prompt
    assert "<output_format>" in prompt
    assert "<action_schema>" in prompt
    assert "<finishing>" in prompt
    assert "<tool_usage>" in prompt
    assert "<parallel_execution>" in prompt
    assert "<reasoning>" in prompt
    assert "<tone>" in prompt
    assert "<error_handling>" in prompt
    assert "<available_tools>" in prompt


def test_build_system_prompt_extra_in_tagged_section() -> None:
    prompt = prompts.build_system_prompt([], extra="Custom instructions")
    assert "<additional_guidance>" in prompt
    assert "Custom instructions" in prompt
    assert "</additional_guidance>" in prompt


def test_build_system_prompt_planning_hints_in_tagged_section() -> None:
    hints = {"constraints": "No external calls"}
    prompt = prompts.build_system_prompt([], planning_hints=hints)
    assert "<planning_constraints>" in prompt
    assert "No external calls" in prompt
    assert "</planning_constraints>" in prompt
