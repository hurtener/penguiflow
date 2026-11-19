"""Tests for the LLM schema module."""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import MagicMock

from penguiflow.llm.schema.plan import (
    OutputMode,
    SchemaPlan,
    choose_output_mode,
    plan_schema,
)
from penguiflow.llm.schema.transformer import (
    JsonSchemaTransformer,
    estimate_object_key_count,
    has_composition_keywords,
    has_refs,
)


class ConcreteTransformer(JsonSchemaTransformer):
    """Concrete implementation for testing base transformer."""

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        return node


class TestJsonSchemaTransformer:
    """Tests for json schema transformer."""

    def test_basic_transform(self) -> None:
        """Basic transform."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        transformer = ConcreteTransformer(schema)
        result = transformer.transform()

        assert result["type"] == "object"
        assert "properties" in result

    def test_is_strict_compatible_default(self) -> None:
        """Is strict compatible default."""
        schema = {"type": "object"}
        transformer = ConcreteTransformer(schema)
        transformer.transform()
        assert transformer.is_strict_compatible is True

    def test_nested_properties(self) -> None:
        """Nested properties."""
        schema = {
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "properties": {"inner": {"type": "string"}},
                }
            },
        }
        transformer = ConcreteTransformer(schema)
        result = transformer.transform()

        assert (
            result["properties"]["outer"]["properties"]["inner"]["type"]
            == "string"
        )

    def test_array_items(self) -> None:
        """Array items."""
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
            },
        }
        transformer = ConcreteTransformer(schema)
        result = transformer.transform()

        assert result["items"]["properties"]["value"]["type"] == "integer"

    def test_anyof(self) -> None:
        """Anyof."""
        schema = {"anyOf": [{"type": "string"}, {"type": "number"}]}
        transformer = ConcreteTransformer(schema)
        result = transformer.transform()

        assert len(result["anyOf"]) == 2

    def test_oneof(self) -> None:
        """Oneof."""
        schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        transformer = ConcreteTransformer(schema)
        result = transformer.transform()

        assert len(result["oneOf"]) == 2

    def test_defs(self) -> None:
        """Defs."""
        schema = {
            "type": "object",
            "properties": {"item": {"$ref": "#/$defs/Item"}},
            "$defs": {"Item": {"type": "string"}},
        }
        transformer = ConcreteTransformer(schema)
        result = transformer.transform()

        assert "$defs" in result
        assert "Item" in result["$defs"]

    def test_additional_properties(self) -> None:
        """Additional properties."""
        schema = {"type": "object", "additionalProperties": {"type": "string"}}
        transformer = ConcreteTransformer(schema)
        result = transformer.transform()

        assert result["additionalProperties"]["type"] == "string"

    def test_remove_unsupported_keywords(self) -> None:
        """Remove unsupported keywords."""

        class StrictTransformer(JsonSchemaTransformer):
            UNSUPPORTED_KEYWORDS: ClassVar[frozenset[str]] = frozenset(
                {"title", "description"}
            )

            def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
                return self._remove_unsupported_keywords(node)

        schema = {
            "type": "object",
            "title": "TestModel",
            "description": "A test model",
            "properties": {"name": {"type": "string"}},
        }
        transformer = StrictTransformer(schema)
        result = transformer.transform()

        assert "title" not in result
        assert "description" not in result
        assert transformer.is_strict_compatible is False

    def test_ensure_required_all(self) -> None:
        """Ensure required all."""

        class RequireAllTransformer(JsonSchemaTransformer):
            def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
                return self._ensure_required_all(node)

        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        }
        transformer = RequireAllTransformer(schema)
        result = transformer.transform()

        assert set(result.get("required", [])) == {"a", "b"}

    def test_add_additional_properties_false(self) -> None:
        """Add additional properties false."""

        class StrictObjectTransformer(JsonSchemaTransformer):
            def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
                return self._add_additional_properties_false(node)

        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        transformer = StrictObjectTransformer(schema, strict=True)
        result = transformer.transform()

        assert result.get("additionalProperties") is False


class TestEstimateObjectKeyCount:
    """Tests for estimate object key count."""

    def test_simple_object(self) -> None:
        """Simple object."""
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "integer"},
                "c": {"type": "boolean"},
            },
        }
        assert estimate_object_key_count(schema) == 3

    def test_nested_objects(self) -> None:
        """Nested objects."""
        schema = {
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "properties": {
                        "inner1": {"type": "string"},
                        "inner2": {"type": "string"},
                    },
                }
            },
        }
        # 1 (outer) + 2 (inner) = 3
        assert estimate_object_key_count(schema) == 3

    def test_array_items(self) -> None:
        """Array items."""
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                },
            },
        }
        assert estimate_object_key_count(schema) == 2

    def test_anyof_variants(self) -> None:
        """Anyof variants."""
        schema = {
            "anyOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
                {"type": "object", "properties": {"b": {"type": "integer"}}},
            ]
        }
        assert estimate_object_key_count(schema) == 2

    def test_defs(self) -> None:
        """Defs."""
        schema = {
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                }
            }
        }
        assert estimate_object_key_count(schema) == 2


class TestHasCompositionKeywords:
    """Tests for has composition keywords."""

    def test_anyof(self) -> None:
        """Anyof."""
        schema = {"anyOf": [{"type": "string"}, {"type": "number"}]}
        assert has_composition_keywords(schema) is True

    def test_oneof(self) -> None:
        """Oneof."""
        schema = {"oneOf": [{"type": "string"}, {"type": "number"}]}
        assert has_composition_keywords(schema) is True

    def test_allof(self) -> None:
        """Allof."""
        schema = {"allOf": [{"type": "object"}, {"properties": {}}]}
        assert has_composition_keywords(schema) is True

    def test_no_composition(self) -> None:
        """No composition."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert has_composition_keywords(schema) is False

    def test_nested_composition(self) -> None:
        """Nested composition."""
        schema = {
            "type": "object",
            "properties": {
                "field": {"anyOf": [{"type": "string"}, {"type": "null"}]}
            },
        }
        assert has_composition_keywords(schema) is True


class TestHasRefs:
    """Tests for has refs."""

    def test_has_ref(self) -> None:
        """Has ref."""
        schema = {"$ref": "#/$defs/Item"}
        assert has_refs(schema) is True

    def test_no_ref(self) -> None:
        """No ref."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert has_refs(schema) is False

    def test_nested_ref(self) -> None:
        """Nested ref."""
        schema = {
            "type": "object",
            "properties": {"item": {"$ref": "#/$defs/Item"}},
        }
        assert has_refs(schema) is True

    def test_ref_in_array(self) -> None:
        """Ref in array."""
        schema = {"anyOf": [{"$ref": "#/$defs/TypeA"}, {"type": "string"}]}
        assert has_refs(schema) is True


class TestOutputMode:
    """Tests for output mode."""

    def test_values(self) -> None:
        """Values."""
        assert OutputMode.NATIVE.value == "native"
        assert OutputMode.TOOLS.value == "tools"
        assert OutputMode.PROMPTED.value == "prompted"


class TestSchemaPlan:
    """Tests for schema plan."""

    def test_create(self) -> None:
        """Create."""
        plan = SchemaPlan(
            requested_schema={"type": "object"},
            transformed_schema={"type": "object"},
            strict_requested=True,
            strict_applied=True,
            compatible_with_native=True,
            compatible_with_tools=True,
        )
        assert plan.strict_applied is True
        assert plan.compatible_with_native is True

    def test_with_reasons(self) -> None:
        """With reasons."""
        plan = SchemaPlan(
            requested_schema={},
            transformed_schema={},
            strict_requested=True,
            strict_applied=False,
            compatible_with_native=False,
            compatible_with_tools=True,
            reasons=("Schema too complex", "Lossy transformation"),
        )
        assert len(plan.reasons) == 2
        assert "Schema too complex" in plan.reasons


class TestPlanSchema:
    """Tests for plan schema."""

    def test_basic_plan(self) -> None:
        """Basic plan."""
        profile = MagicMock()
        profile.strict_mode_default = True
        profile.provider_name = "openai"
        profile.max_schema_keys = None
        profile.native_structured_kind = "response_format"
        profile.supports_schema_guided_output = True
        profile.supports_tools = True

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        plan = plan_schema(profile, schema)

        assert plan.requested_schema == schema
        assert plan.transformed_schema is not None
        assert plan.compatible_with_native is True

    def test_plan_with_key_limit(self) -> None:
        """Plan with key limit."""
        profile = MagicMock()
        profile.strict_mode_default = True
        profile.provider_name = "databricks"
        profile.max_schema_keys = 5
        profile.native_structured_kind = "databricks_constrained_decoding"
        profile.supports_schema_guided_output = True
        profile.supports_tools = True

        schema = {
            "type": "object",
            "properties": {f"field{i}": {"type": "string"} for i in range(10)},
        }

        plan = plan_schema(profile, schema)

        assert plan.compatible_with_native is False
        assert any("key limit" in r.lower() for r in plan.reasons)

    def test_plan_no_native_support(self) -> None:
        """Plan no native support."""
        profile = MagicMock()
        profile.strict_mode_default = False
        profile.provider_name = "custom"
        profile.max_schema_keys = None
        profile.native_structured_kind = None
        profile.supports_schema_guided_output = False
        profile.supports_tools = True

        schema = {"type": "object"}

        plan = plan_schema(profile, schema)

        assert plan.compatible_with_native is False


class TestChooseOutputMode:
    """Tests for choose output mode."""

    def test_choose_native_when_supported(self) -> None:
        """Choose native when supported."""
        profile = MagicMock()
        profile.default_output_mode = "native"
        profile.strict_mode_default = True
        profile.provider_name = "openai"
        profile.max_schema_keys = None
        profile.native_structured_kind = "response_format"
        profile.supports_schema_guided_output = True
        profile.supports_tools = True

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        mode, plan = choose_output_mode(profile, schema)

        assert mode == OutputMode.NATIVE
        assert plan.compatible_with_native is True

    def test_fallback_to_tools(self) -> None:
        """Fallback to tools."""
        profile = MagicMock()
        profile.default_output_mode = "native"
        profile.strict_mode_default = True
        profile.provider_name = "custom"
        profile.max_schema_keys = None
        profile.native_structured_kind = None
        profile.supports_schema_guided_output = False
        profile.supports_tools = True

        schema = {"type": "object"}

        mode, _plan = choose_output_mode(profile, schema)

        assert mode == OutputMode.TOOLS

    def test_fallback_to_prompted(self) -> None:
        """Fallback to prompted."""
        profile = MagicMock()
        profile.default_output_mode = "native"
        profile.strict_mode_default = False
        profile.provider_name = "minimal"
        profile.max_schema_keys = None
        profile.native_structured_kind = None
        profile.supports_schema_guided_output = False
        profile.supports_tools = False

        schema = {"type": "object"}

        mode, _plan = choose_output_mode(profile, schema)

        assert mode == OutputMode.PROMPTED
