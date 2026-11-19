"""Base JSON schema transformer for the LLM layer.

Provides recursive schema walking and transformation capabilities. Adapted from
PydanticAI patterns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator


# Keys whose value is a mapping of names to subschemas.
_SCHEMA_MAP_KEYS = frozenset({"properties", "$defs", "patternProperties"})
# Keys whose value is a single subschema.
_SCHEMA_KEYS = frozenset(
    {"items", "additionalProperties", "if", "then", "else", "not"}
)
# Keys whose value is a list of subschemas.
_SCHEMA_LIST_KEYS = frozenset(
    {"items", "anyOf", "oneOf", "allOf", "prefixItems"}
)


class JsonSchemaTransformer(ABC):
    """Base class for provider-specific JSON schema transformations.

    Walks the schema recursively, applying transformations at each level.
    Handles recursive walking, optional $ref inlining, and keyword stripping.

    Provider-specific subclasses override `_transform_node()` to apply
    their particular transformations.

    Usage:
        transformer = OpenAIJsonSchemaTransformer(schema, strict=True)
        transformed = transformer.transform()
        if not transformer.is_strict_compatible:
            # Schema had lossy transformations
    """

    # Keywords that should be removed by default (can be overridden)
    UNSUPPORTED_KEYWORDS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, schema: dict[str, Any], *, strict: bool = True) -> None:
        """Initialize the transformer.

        Args:
            schema: The JSON schema to transform.
            strict: Whether to use strict mode (provider-specific meaning).
        """
        self.original_schema = deepcopy(schema)
        self.strict = strict
        self.is_strict_compatible = True
        self._prefer_inline_refs = False
        self._refs_stack: list[str] = []
        self._recursive_refs: set[str] = set()
        self._transformed_defs: dict[str, dict[str, Any]] = {}
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        """Warnings collected while transforming the schema."""
        return list(self._warnings)

    @property
    def transformed_defs(self) -> dict[str, dict[str, Any]]:
        """``$defs`` entries already inlined, by definition name."""
        return dict(self._transformed_defs)

    @property
    def recursive_refs(self) -> frozenset[str]:
        """``$ref`` targets found to be recursive."""
        return frozenset(self._recursive_refs)

    def transform(self) -> dict[str, Any]:
        """Transform the schema for this provider.

        Returns:
            The transformed schema.
        """
        result = self._walk(self.original_schema)
        return self._finalize(result)

    def _walk(self, node: dict[str, Any]) -> dict[str, Any]:
        """Recursively walk and transform schema nodes.

        Args:
            node: A schema node to transform.

        Returns:
            The transformed node.
        """
        if "$ref" in node:
            return self._handle_ref(node)
        result = {
            key: self._walk_value(key, value) for key, value in node.items()
        }
        return self._transform_node(result)

    def _walk_value(self, key: str, value: object) -> object:
        """Walk the subschemas held by ``key``; other values pass through."""
        if isinstance(value, dict):
            if key in _SCHEMA_MAP_KEYS:
                return {
                    name: self._walk(sub) if isinstance(sub, dict) else sub
                    for name, sub in value.items()
                }
            if key in _SCHEMA_KEYS:
                return self._walk(value)
        elif isinstance(value, list) and key in _SCHEMA_LIST_KEYS:
            return [self._walk(v) if isinstance(v, dict) else v for v in value]
        return value

    @abstractmethod
    def _transform_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """Apply provider-specific transformations to a node.

        This method is called for each node after recursive walking.
        Subclasses should override to apply their transformations.

        Args:
            node: The node to transform (after children have been walked).

        Returns:
            The transformed node.
        """
        ...

    def _handle_ref(self, node: dict[str, Any]) -> dict[str, Any]:
        """Handle $ref, detecting recursive references.

        Args:
            node: A node containing a $ref.

        Returns:
            Either the inlined definition or the original $ref node.
        """
        ref = node["$ref"]

        # Check for recursive reference
        if ref in self._refs_stack:
            self._recursive_refs.add(ref)
            # Keep recursive refs as-is or mark as incompatible
            self.is_strict_compatible = False
            self._warnings.append(f"Recursive reference detected: {ref}")
            return node

        # Inline if preferred and possible
        if self._prefer_inline_refs and ref.startswith("#/$defs/"):
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

        # Keep as $ref when allowed by provider
        return node

    def _finalize(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Final cleanup after transformation.

        Override to add post-processing steps.

        Args:
            schema: The transformed schema.

        Returns:
            The finalized schema.
        """
        # Remove $defs if all refs were inlined and no recursive refs exist
        if (
            self._prefer_inline_refs
            and not self._recursive_refs
            and "$defs" in schema
        ):
            del schema["$defs"]

        return schema

    def _remove_unsupported_keywords(
        self, node: dict[str, Any]
    ) -> dict[str, Any]:
        """Remove keywords that aren't supported by this provider.

        Args:
            node: The node to clean.

        Returns:
            The cleaned node.
        """
        for keyword in self.UNSUPPORTED_KEYWORDS:
            if keyword in node:
                del node[keyword]
                self.is_strict_compatible = False

        return node

    def _ensure_required_all(self, node: dict[str, Any]) -> dict[str, Any]:
        """Ensure all properties are marked as required (strict mode).

        Args:
            node: An object-type node.

        Returns:
            The node with all properties required.
        """
        if node.get("type") == "object" and "properties" in node:
            node["required"] = list(node["properties"].keys())
        return node

    def _add_additional_properties_false(
        self, node: dict[str, Any]
    ) -> dict[str, Any]:
        """Add additionalProperties: false for object types (strict mode).

        Args:
            node: An object-type node.

        Returns:
            The node with additionalProperties: false.
        """
        if node.get("type") == "object" and self.strict:
            node["additionalProperties"] = False
        return node


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------


def _nested_schemas(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield the subschemas under ``items``, compositions and ``$defs``."""
    items = schema.get("items")
    if isinstance(items, dict):
        yield items
    for key in ("anyOf", "oneOf", "allOf"):
        options = schema.get(key)
        if isinstance(options, list):
            yield from (v for v in options if isinstance(v, dict))
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        yield from (v for v in defs.values() if isinstance(v, dict))


def estimate_object_key_count(schema: dict[str, Any]) -> int:
    """Best-effort count of object property keys (heuristic).

    Used to check against provider limits (e.g., Databricks max 64 keys).

    Args:
        schema: The JSON schema to analyze.

    Returns:
        Estimated total key count.
    """
    count = 0
    properties = schema.get("properties")
    if schema.get("type") == "object" and isinstance(properties, dict):
        count += len(properties)
        count += sum(
            estimate_object_key_count(v)
            for v in properties.values()
            if isinstance(v, dict)
        )
    return count + sum(
        estimate_object_key_count(child) for child in _nested_schemas(schema)
    )


def has_composition_keywords(schema: dict[str, Any]) -> bool:
    """Check if schema uses anyOf/oneOf/allOf composition.

    Args:
        schema: The JSON schema to check.

    Returns:
        True if composition keywords are present.
    """

    def _check(node: object) -> bool:
        if not isinstance(node, dict):
            return False
        if any(k in node for k in ("anyOf", "oneOf", "allOf")):
            return True
        children: list[object] = []
        for key in ("properties", "$defs"):
            value = node.get(key)
            if isinstance(value, dict):
                children.extend(value.values())
        items = node.get("items")
        if isinstance(items, dict):
            children.append(items)
        return any(_check(child) for child in children)

    return _check(schema)


def has_refs(schema: dict[str, Any]) -> bool:
    """Check if schema uses $ref.

    Args:
        schema: The JSON schema to check.

    Returns:
        True if $ref is present.
    """

    def _check(node: object) -> bool:
        if not isinstance(node, dict):
            return False

        if "$ref" in node:
            return True

        has_refs_in_values = any(
            _check(v) for v in node.values() if isinstance(v, (dict, list))
        )
        has_refs_in_lists = any(
            _check(item)
            for v in node.values()
            if isinstance(v, list)
            for item in v
        )
        return has_refs_in_values or has_refs_in_lists

    return _check(schema)
