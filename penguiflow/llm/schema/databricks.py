"""Databricks JSON schema transformer.

Handles Databricks' constrained decoding schema requirements.
"""

from __future__ import annotations

from typing import Any

from .transformer import JsonSchemaTransformer


class DatabricksJsonSchemaTransformer(JsonSchemaTransformer):
    """Databricks schema transformer.

    Databricks uses constrained decoding with specific limitations:
    - No anyOf, oneOf, allOf, $ref, pattern support
    - Maximum 64 keys in schema
    - Flatten nested structures where possible

    Transformations:
    - Remove unsupported keywords
    - Simplify nullable unions (anyOf with null)
    - Track key count for validation
    - Add additionalProperties: false

    Reference: https://docs.databricks.com/aws/en/machine-learning/model-serving/structured-outputs
    """

    UNSUPPORTED_KEYWORDS = {
        "pattern",
        "patternProperties",
        "minLength",
        "maxLength",
        "minProperties",
        "maxProperties",
        "minItems",
        "maxItems",
        "$ref",
        "$defs",
        "$schema",
        "title",
        "examples",
        "default",
    }

    # Databricks limits
    MAX_KEYS = 64
    MAX_TOOLS = 32
    MAX_TOOL_SCHEMA_KEYS = 16

    def __init__(self, schema: dict[str, Any], *, strict: bool = True):
        """Initialize with key tracking."""
        super().__init__(schema, strict=strict)
        self._prefer_inline_refs = True  # Must inline all refs
        self._key_count = 0

    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """Apply Databricks-specific transformations."""
        # Remove unsupported keywords
        for keyword in self.UNSUPPORTED_KEYWORDS:
            if keyword in node:
                node.pop(keyword)
                self.is_strict_compatible = False

        # Handle composition keywords - try to simplify or mark incompatible
        for composition in ("anyOf", "oneOf", "allOf"):
            if composition in node:
                options = node.get(composition, [])

                # Try to simplify: if it's just [type, null], make it the non-null type
                if len(options) == 2:
                    types = [o.get("type") for o in options if isinstance(o, dict)]
                    if "null" in types:
                        non_null = [o for o in options if isinstance(o, dict) and o.get("type") != "null"]
                        if non_null:
                            node.pop(composition)
                            # Merge the non-null option into this node
                            for key, value in non_null[0].items():
                                if key not in node:
                                    node[key] = value
                            continue

                # Can't simplify - mark as incompatible
                node.pop(composition)
                self.is_strict_compatible = False
                self._warnings.append(f"Removed unsupported {composition} composition.")

        # Track key count for validation
        if node.get("type") == "object" and "properties" in node:
            self._key_count += len(node["properties"])
            if self._key_count > self.MAX_KEYS:
                self.is_strict_compatible = False
                self._warnings.append(f"Schema exceeds {self.MAX_KEYS}-key limit.")

        # Add additionalProperties: false for strict mode
        if node.get("type") == "object" and self.strict:
            node["additionalProperties"] = False
            # Ensure required is set
            if "properties" in node and "required" not in node:
                node["required"] = list(node["properties"].keys())

        return node

    def _handle_ref(self, node: dict[str, Any]) -> dict[str, Any]:
        """Handle $ref by inlining - Databricks doesn't support $ref."""
        ref = node.get("$ref", "")

        # Check for recursive reference
        if ref in self._refs_stack:
            self._recursive_refs.add(ref)
            self.is_strict_compatible = False
            self._warnings.append(f"Recursive reference not supported: {ref}")
            # Return a placeholder object
            return {"type": "object", "description": f"[Recursive ref: {ref}]"}

        # Inline the reference
        if ref.startswith("#/$defs/"):
            def_name = ref.removeprefix("#/$defs/")
            defs = self.original_schema.get("$defs", {})

            if def_name in defs:
                from copy import deepcopy

                if def_name in self._transformed_defs:
                    return deepcopy(self._transformed_defs[def_name])

                self._refs_stack.append(ref)
                inlined = self._walk(deepcopy(defs[def_name]))
                self._refs_stack.pop()
                self._transformed_defs[def_name] = inlined
                return deepcopy(inlined)

        # Unknown ref - return placeholder
        self.is_strict_compatible = False
        self._warnings.append(f"Could not resolve ref: {ref}")
        return {"type": "string", "description": f"[Unresolved ref: {ref}]"}

    def _finalize(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Remove $defs (already inlined) and validate key count."""
        # Remove $defs
        schema.pop("$defs", None)

        # Final key count check
        if self._key_count > self.MAX_KEYS:
            self._warnings.append(f"Total key count ({self._key_count}) exceeds Databricks limit ({self.MAX_KEYS}).")

        return schema
