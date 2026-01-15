"""OpenAI JSON schema transformer.

Handles OpenAI's strict mode requirements for structured outputs.
"""

from __future__ import annotations

from typing import Any

from .transformer import JsonSchemaTransformer


class OpenAIJsonSchemaTransformer(JsonSchemaTransformer):
    """OpenAI strict mode schema transformer.

    Transformations for OpenAI structured outputs:
    - Add additionalProperties: false to all objects
    - Mark all properties as required
    - Remove unsupported keywords (minLength, maxLength, pattern, etc.)
    - Convert oneOf to anyOf (OpenAI preference)

    Reference: https://platform.openai.com/docs/guides/structured-outputs
    """

    UNSUPPORTED_KEYWORDS = {
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "patternProperties",
    }

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """Apply OpenAI-specific transformations."""
        # Remove unsupported keywords
        for keyword in self.UNSUPPORTED_KEYWORDS:
            if keyword in node:
                del node[keyword]
                self.is_strict_compatible = False

        # Handle object types
        if node.get("type") == "object":
            if self.strict:
                node["additionalProperties"] = False
            if "properties" in node:
                node["required"] = list(node["properties"].keys())

        # Convert oneOf to anyOf (OpenAI preference)
        if "oneOf" in node:
            node["anyOf"] = node.pop("oneOf")

        # Remove title and $schema at top level
        node.pop("title", None)
        node.pop("$schema", None)

        return node
