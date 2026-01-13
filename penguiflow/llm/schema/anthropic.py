"""Anthropic JSON schema transformer.

Handles Anthropic's schema requirements for tool use.
"""

from __future__ import annotations

from typing import Any

from .transformer import JsonSchemaTransformer


class AnthropicJsonSchemaTransformer(JsonSchemaTransformer):
    """Anthropic schema transformer.

    Transformations for Anthropic tool use:
    - Remove constraints and relocate to description
    - Add additionalProperties: false when strict
    - Strip title and $schema fields

    Anthropic uses tool definitions with JSON schemas, but with
    some constraints needing to be expressed in descriptions.

    Reference: https://docs.anthropic.com/en/docs/tool-use
    """

    RELOCATE_TO_DESCRIPTION = {
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
    }

    def __init__(self, schema: dict[str, Any], *, strict: bool = False):
        """Initialize with strict=False as Anthropic requires lossy transformation."""
        super().__init__(schema, strict=strict)

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """Apply Anthropic-specific transformations."""
        # Relocate constraints to description
        constraints = []
        for keyword in self.RELOCATE_TO_DESCRIPTION:
            if keyword in node:
                value = node.pop(keyword)
                constraints.append(f"{keyword}: {value}")

        if constraints:
            desc = node.get("description", "")
            constraint_str = " | ".join(constraints)
            if desc:
                node["description"] = f"{desc} [{constraint_str}]"
            else:
                node["description"] = f"[{constraint_str}]"
            self.is_strict_compatible = False

        # Remove title and $schema
        node.pop("title", None)
        node.pop("$schema", None)

        # Handle object types with strict mode
        if node.get("type") == "object" and self.strict:
            node["additionalProperties"] = False

        return node
