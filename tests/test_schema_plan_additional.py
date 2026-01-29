"""Additional coverage for schema planning.

Focuses on exercising schema transformers and compatibility flags.
"""

from __future__ import annotations

from penguiflow.llm.profiles import ModelProfile
from penguiflow.llm.schema.plan import OutputMode, choose_output_mode, plan_schema


def test_plan_schema_runs_transformer_and_marks_native_incompatible() -> None:
    profile = ModelProfile(
        supports_schema_guided_output=True,
        supports_tools=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_schema_keys=64,
    )

    schema = {
        "type": "object",
        "properties": {
            "maybe": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                ]
            },
            "node": {"$ref": "#/$defs/Node"},
        },
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/Node"}},
            }
        },
    }

    plan = plan_schema(profile, schema, mode=OutputMode.NATIVE)
    assert plan.strict_requested is True
    assert plan.strict_applied is False  # lossy transforms disable strict
    assert plan.compatible_with_native is False  # composition + recursive refs
    assert plan.has_recursive_refs is True
    assert any("strict disabled" in r.lower() for r in plan.reasons)

    mode, plan2 = choose_output_mode(profile, schema)
    assert mode in {OutputMode.TOOLS, OutputMode.PROMPTED}
    assert plan2.requested_schema == schema
