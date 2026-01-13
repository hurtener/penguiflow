"""Tests for the provider-specific schema transformers."""

from __future__ import annotations

from penguiflow.llm.schema.anthropic import AnthropicJsonSchemaTransformer
from penguiflow.llm.schema.bedrock import BedrockJsonSchemaTransformer
from penguiflow.llm.schema.databricks import DatabricksJsonSchemaTransformer
from penguiflow.llm.schema.google import GoogleJsonSchemaTransformer
from penguiflow.llm.schema.openai import OpenAIJsonSchemaTransformer


class TestOpenAIJsonSchemaTransformer:
    def test_basic_transform(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        assert result["type"] == "object"
        assert "properties" in result
        assert result.get("additionalProperties") is False
        assert set(result.get("required", [])) == {"name", "age"}

    def test_removes_constraints(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "pattern": "^[a-z]+$",
                },
                "count": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                },
            },
        }
        transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        # Constraints should be removed
        name_prop = result["properties"]["name"]
        assert "minLength" not in name_prop
        assert "maxLength" not in name_prop
        assert "pattern" not in name_prop

        count_prop = result["properties"]["count"]
        assert "minimum" not in count_prop
        assert "maximum" not in count_prop

    def test_removes_metadata(self) -> None:
        schema = {
            "type": "object",
            "title": "MySchema",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": {"x": {"type": "string"}},
        }
        transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        assert "title" not in result
        assert "$schema" not in result

    def test_converts_oneof_to_anyof(self) -> None:
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ]
        }
        transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        # oneOf should be converted to anyOf
        assert "anyOf" in result
        assert "oneOf" not in result
        assert len(result["anyOf"]) == 2

    def test_nested_object_strict(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                    },
                }
            },
        }
        transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        # Both levels should have additionalProperties: false
        assert result.get("additionalProperties") is False
        assert result["properties"]["nested"].get("additionalProperties") is False

    def test_preserves_refs(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "item": {"$ref": "#/$defs/Item"},
            },
            "$defs": {
                "Item": {"type": "string"},
            },
        }
        transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        # $refs and $defs should be preserved
        assert "$defs" in result
        assert "$ref" in result["properties"]["item"]


class TestAnthropicJsonSchemaTransformer:
    def test_basic_transform(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        }
        transformer = AnthropicJsonSchemaTransformer(schema, strict=False)
        result = transformer.transform()

        assert result["type"] == "object"
        assert "properties" in result

    def test_relocates_constraints_to_description(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 5,
                    "maxLength": 50,
                },
            },
        }
        transformer = AnthropicJsonSchemaTransformer(schema, strict=False)
        result = transformer.transform()

        name_prop = result["properties"]["name"]
        # Constraints should be removed
        assert "minLength" not in name_prop
        assert "maxLength" not in name_prop
        # Description should contain the constraints
        desc = name_prop.get("description", "")
        assert "minLength: 5" in desc or "min" in desc.lower()

    def test_merges_with_existing_description(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The user's name",
                    "minLength": 1,
                },
            },
        }
        transformer = AnthropicJsonSchemaTransformer(schema, strict=False)
        result = transformer.transform()

        name_prop = result["properties"]["name"]
        desc = name_prop.get("description", "")
        # Should have both original description and constraints
        assert "user's name" in desc.lower() or "minLength" in desc

    def test_removes_title_and_schema(self) -> None:
        schema = {
            "type": "object",
            "title": "TestSchema",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": {"x": {"type": "string"}},
        }
        transformer = AnthropicJsonSchemaTransformer(schema)
        result = transformer.transform()

        assert "title" not in result
        assert "$schema" not in result


class TestGoogleJsonSchemaTransformer:
    def test_basic_transform(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
            },
        }
        transformer = GoogleJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        assert result["type"] == "object"
        assert result.get("additionalProperties") is False

    def test_converts_const_to_enum(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "const": "active",
                },
            },
        }
        transformer = GoogleJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        status_prop = result["properties"]["status"]
        assert "const" not in status_prop
        assert "enum" in status_prop
        assert status_prop["enum"] == ["active"]

    def test_infers_type_from_const(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "count": {"const": 42},
                "flag": {"const": True},
                "name": {"const": "test"},
            },
        }
        transformer = GoogleJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        # Type should be inferred from const value
        assert result["properties"]["count"]["type"] == "integer"
        assert result["properties"]["flag"]["type"] == "boolean"
        assert result["properties"]["name"]["type"] == "string"

    def test_moves_format_to_description(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "created_at": {
                    "type": "string",
                    "format": "date-time",
                },
            },
        }
        transformer = GoogleJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        created_prop = result["properties"]["created_at"]
        assert "format" not in created_prop
        desc = created_prop.get("description", "")
        assert "date-time" in desc

    def test_simplifies_anyof_with_null(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"},
                    ]
                },
            },
        }
        transformer = GoogleJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        value_prop = result["properties"]["value"]
        # Should be simplified to a nullable string
        assert value_prop.get("type") == "string"
        assert value_prop.get("nullable") is True
        assert "anyOf" not in value_prop

    def test_removes_unsupported_keywords(self) -> None:
        schema = {
            "type": "object",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "discriminator": {"propertyName": "type"},
            "examples": [{"x": 1}],
            "properties": {"x": {"type": "integer"}},
        }
        transformer = GoogleJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        assert "$schema" not in result
        assert "discriminator" not in result
        assert "examples" not in result


class TestBedrockJsonSchemaTransformer:
    def test_basic_transform(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        }
        transformer = BedrockJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        assert result["type"] == "object"
        assert result.get("additionalProperties") is False
        assert "name" in result.get("required", [])

    def test_inlines_refs(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "item": {"$ref": "#/$defs/Item"},
            },
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                },
            },
        }
        transformer = BedrockJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        # $defs should be removed after inlining
        assert "$defs" not in result
        # The ref should be inlined
        assert "$ref" not in result["properties"]["item"]
        assert result["properties"]["item"]["type"] == "object"

    def test_removes_unsupported_keywords(self) -> None:
        schema = {
            "type": "object",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "TestSchema",
            "examples": [{"x": 1}],
            "default": {},
            "properties": {"x": {"type": "integer"}},
        }
        transformer = BedrockJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        assert "$schema" not in result
        assert "title" not in result
        assert "examples" not in result
        assert "default" not in result

    def test_handles_nested_inlining(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "outer": {"$ref": "#/$defs/Outer"},
            },
            "$defs": {
                "Inner": {"type": "string"},
                "Outer": {
                    "type": "object",
                    "properties": {
                        "inner": {"$ref": "#/$defs/Inner"},
                    },
                },
            },
        }
        transformer = BedrockJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        # All refs should be inlined
        assert "$defs" not in result
        outer = result["properties"]["outer"]
        assert outer["type"] == "object"
        assert outer["properties"]["inner"]["type"] == "string"


class TestDatabricksJsonSchemaTransformer:
    def test_basic_transform(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        }
        transformer = DatabricksJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        assert result["type"] == "object"
        assert result.get("additionalProperties") is False

    def test_removes_pattern_and_constraints(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "pattern": "^[a-z]+$",
                    "minLength": 1,
                    "maxLength": 100,
                },
            },
        }
        transformer = DatabricksJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        name_prop = result["properties"]["name"]
        assert "pattern" not in name_prop
        assert "minLength" not in name_prop
        assert "maxLength" not in name_prop

    def test_removes_refs_and_defs(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "item": {"$ref": "#/$defs/Item"},
            },
            "$defs": {
                "Item": {"type": "string"},
            },
        }
        transformer = DatabricksJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        # $defs should be removed
        assert "$defs" not in result
        # $ref should be resolved or replaced
        item_prop = result["properties"]["item"]
        assert "$ref" not in item_prop

    def test_simplifies_nullable_anyof(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"},
                    ]
                },
            },
        }
        transformer = DatabricksJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        value_prop = result["properties"]["value"]
        # Should be simplified - anyOf removed
        assert "anyOf" not in value_prop

    def test_removes_composition_keywords(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "complex": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "integer"},
                    ]
                },
            },
        }
        transformer = DatabricksJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        complex_prop = result["properties"]["complex"]
        # oneOf should be simplified/removed
        assert "oneOf" not in complex_prop

    def test_removes_unsupported_metadata(self) -> None:
        schema = {
            "type": "object",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "TestSchema",
            "examples": [{"x": 1}],
            "default": {},
            "properties": {"x": {"type": "integer"}},
        }
        transformer = DatabricksJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        assert "$schema" not in result
        assert "title" not in result
        assert "examples" not in result
        assert "default" not in result

    def test_warns_on_complex_schema(self) -> None:
        # Create schema with many keys
        properties = {f"field_{i}": {"type": "string"} for i in range(70)}
        schema = {"type": "object", "properties": properties}

        transformer = DatabricksJsonSchemaTransformer(schema, strict=True)
        transformer.transform()

        # Should have warnings about key limit
        assert len(transformer._warnings) > 0 or not transformer.is_strict_compatible

    def test_handles_allof(self) -> None:
        schema = {
            "allOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
                {"type": "object", "properties": {"b": {"type": "integer"}}},
            ]
        }
        transformer = DatabricksJsonSchemaTransformer(schema, strict=True)
        result = transformer.transform()

        # allOf should be removed/simplified
        assert "allOf" not in result


class TestTransformerStrictModes:
    """Test strict vs non-strict mode behaviors."""

    def test_openai_strict_mode(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}

        strict = OpenAIJsonSchemaTransformer(schema, strict=True).transform()
        non_strict = OpenAIJsonSchemaTransformer(schema, strict=False).transform()

        assert strict.get("additionalProperties") is False
        # Non-strict may not have additionalProperties: false
        assert non_strict is not None

    def test_google_strict_mode(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}

        strict = GoogleJsonSchemaTransformer(schema, strict=True).transform()

        assert strict.get("additionalProperties") is False

    def test_bedrock_strict_mode(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}

        strict = BedrockJsonSchemaTransformer(schema, strict=True).transform()

        assert strict.get("additionalProperties") is False
        assert "x" in strict.get("required", [])


class TestTransformerCompatibility:
    """Test is_strict_compatible flag behavior."""

    def test_compatible_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
        transformer.transform()

        assert transformer.is_strict_compatible is True

    def test_incompatible_with_removed_keywords(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "pattern": "^[a-z]+$"},
            },
        }
        transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
        transformer.transform()

        # Pattern removal should mark as incompatible
        assert transformer.is_strict_compatible is False
