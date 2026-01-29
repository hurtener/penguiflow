"""Google/Gemini JSON schema transformer.

Handles Google's schema requirements for structured outputs.
"""

from __future__ import annotations

from typing import Any

from .transformer import JsonSchemaTransformer


class GoogleJsonSchemaTransformer(JsonSchemaTransformer):
    """Google/Gemini schema transformer.

    Transformations for Google structured output:
    - Convert const to enum with single value
    - Append format to description
    - Remove exclusiveMinimum/exclusiveMaximum
    - Remove $schema, discriminator, examples, title
    - Handle nullable fields via anyOf simplification

    Reference: https://ai.google.dev/gemini-api/docs/structured-output
    """

    UNSUPPORTED_KEYWORDS = {
        "$schema",
        "discriminator",
        "examples",
        "title",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """Apply Google-specific transformations."""
        # Convert const to enum (Gemini doesn't support const)
        if "const" in node:
            const_val = node.pop("const")
            node["enum"] = [const_val]
            if "type" not in node:
                node["type"] = self._infer_type(const_val)

        # Append format to description
        if "format" in node:
            fmt = node.pop("format")
            desc = node.get("description", "")
            if desc:
                node["description"] = f"{desc} (format: {fmt})"
            else:
                node["description"] = f"(format: {fmt})"

        # Remove unsupported keywords
        for keyword in self.UNSUPPORTED_KEYWORDS:
            node.pop(keyword, None)

        # Handle anyOf with null for optional fields
        if "anyOf" in node:
            options = node.get("anyOf", [])
            if len(options) == 2:
                types = [o.get("type") for o in options]
                if "null" in types:
                    non_null = [o for o in options if o.get("type") != "null"]
                    if non_null:
                        node.pop("anyOf")
                        node.update(non_null[0])
                        node["nullable"] = True

        # Handle object types
        if node.get("type") == "object" and self.strict:
            # Only enforce additionalProperties=false when the object has an explicit
            # "properties" shape. For dict-like schemas (no properties, or a schema-valued
            # additionalProperties), forcing a boolean here breaks common patterns like
            # `dict[str, Any]` (used for PlannerAction.args), making all keys invalid.
            if "properties" in node and "additionalProperties" not in node:
                node["additionalProperties"] = False

        return node

    def _infer_type(self, value: Any) -> str:
        """Infer JSON schema type from a Python value."""
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        return "string"
