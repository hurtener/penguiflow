"""Google/Gemini JSON schema transformer.

Handles Google's schema requirements for structured outputs.
"""

from __future__ import annotations

from typing import Any, ClassVar

from typing_extensions import override

from .transformer import JsonSchemaTransformer


def _format_to_description(node: dict[str, Any]) -> None:
    """Move ``format`` into the description (Gemini ignores it)."""
    if "format" not in node:
        return
    fmt = node.pop("format")
    desc = node.get("description", "")
    node["description"] = (
        f"{desc} (format: {fmt})" if desc else f"(format: {fmt})"
    )


def _collapse_nullable_any_of(node: dict[str, Any]) -> None:
    """Turn ``anyOf: [X, null]`` into ``X`` with ``nullable: true``."""
    options = node.get("anyOf")
    if not isinstance(options, list) or len(options) != 2:
        return
    if "null" not in [o.get("type") for o in options]:
        return
    non_null = [o for o in options if o.get("type") != "null"]
    if non_null:
        node.pop("anyOf")
        node.update(non_null[0])
        node["nullable"] = True


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

    UNSUPPORTED_KEYWORDS: ClassVar[frozenset[str]] = frozenset(
        {
            "$schema",
            "discriminator",
            "examples",
            "title",
            "exclusiveMinimum",
            "exclusiveMaximum",
        }
    )

    @override
    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """Apply Google-specific transformations."""
        self._const_to_enum(node)
        _format_to_description(node)

        # Remove unsupported keywords
        for keyword in self.UNSUPPORTED_KEYWORDS:
            node.pop(keyword, None)

        _collapse_nullable_any_of(node)

        # Only enforce additionalProperties=false when the object has an
        # explicit "properties" shape. For dict-like schemas (no properties,
        # or a schema-valued additionalProperties), forcing a boolean here
        # breaks common patterns like `dict[str, Any]` (used for
        # PlannerAction.args), making all keys invalid.
        if (
            node.get("type") == "object"
            and self.strict
            and "properties" in node
            and "additionalProperties" not in node
        ):
            node["additionalProperties"] = False

        return node

    def _const_to_enum(self, node: dict[str, Any]) -> None:
        """Convert ``const`` to a one-value ``enum`` (Gemini lacks const)."""
        if "const" not in node:
            return
        const_val = node.pop("const")
        node["enum"] = [const_val]
        if "type" not in node:
            node["type"] = self._infer_type(const_val)

    def _infer_type(self, value: object) -> str:
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
