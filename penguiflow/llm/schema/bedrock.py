"""AWS Bedrock JSON schema transformer.

Handles Bedrock's schema requirements for tool use via Converse API.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .transformer import JsonSchemaTransformer


class BedrockJsonSchemaTransformer(JsonSchemaTransformer):
    """AWS Bedrock schema transformer.

    Uses inline definitions strategy - inlines all $defs except recursive ones.
    Bedrock Converse API requires schemas to be self-contained without external refs.

    Transformations:
    - Inline all $defs (except recursive)
    - Add additionalProperties: false for objects
    - Remove unsupported keywords

    Reference: https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html
    """

    UNSUPPORTED_KEYWORDS = {
        "$schema",
        "title",
        "examples",
        "default",
    }

    def __init__(self, schema: dict[str, Any], *, strict: bool = True):
        """Initialize with inline refs enabled."""
        super().__init__(schema, strict=strict)
        self._prefer_inline_refs = True

    def _handle_ref(self, node: dict[str, Any]) -> dict[str, Any]:
        """Handle $ref by inlining the definition."""
        ref = node["$ref"]

        # Check for recursive reference
        if ref in self._refs_stack:
            self._recursive_refs.add(ref)
            self.is_strict_compatible = False
            self._warnings.append(f"Recursive reference detected (kept as-is): {ref}")
            return node

        # Inline the reference
        if ref.startswith("#/$defs/"):
            def_name = ref.removeprefix("#/$defs/")
            defs = self.original_schema.get("$defs", {})

            if def_name in defs:
                # Check if already transformed
                if def_name in self._transformed_defs:
                    return deepcopy(self._transformed_defs[def_name])

                self._refs_stack.append(ref)
                inlined = self._walk(deepcopy(defs[def_name]))
                self._refs_stack.pop()
                self._transformed_defs[def_name] = inlined
                return deepcopy(inlined)

        # Unknown ref format - keep as-is
        self._warnings.append(f"Unknown ref format (kept as-is): {ref}")
        return node

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """Apply Bedrock-specific transformations."""
        # Remove unsupported keywords
        for keyword in self.UNSUPPORTED_KEYWORDS:
            node.pop(keyword, None)

        # Handle object types
        if node.get("type") == "object":
            if self.strict:
                node["additionalProperties"] = False
            if "properties" in node and "required" not in node:
                # Bedrock prefers explicit required
                node["required"] = list(node["properties"].keys())

        return node

    def _finalize(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Remove $defs after inlining."""
        # Always remove $defs for Bedrock as we've inlined everything
        if "$defs" in schema and not self._recursive_refs:
            del schema["$defs"]
        return schema
