from __future__ import annotations

from penguiflow.planner.llm import _build_planner_action_schema_conditional_finish


def test_planner_action_schema_requires_answer_on_final_response() -> None:
    schema = _build_planner_action_schema_conditional_finish()
    assert "allOf" in schema
    assert isinstance(schema["allOf"], list)
    assert len(schema["allOf"]) == 2

    conditional = schema["allOf"][1]
    assert "if" in conditional
    assert "then" in conditional
    assert conditional["if"]["properties"]["next_node"]["enum"] == ["final_response"]

    then_args = conditional["then"]["properties"]["args"]
    assert then_args["required"] == ["answer"]
    assert then_args["properties"]["answer"]["type"] == "string"

