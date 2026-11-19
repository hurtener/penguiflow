"""Validation helpers for rich output component payloads."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jsonschema import Draft7Validator

if TYPE_CHECKING:
    from .registry import ComponentRegistry


@dataclass(frozen=True)
class ValidationLimits:
    """Payload size and nesting limits for component validation."""

    max_payload_bytes: int
    max_total_bytes: int
    max_depth: int = 6


class RichOutputValidationError(ValueError):
    """Raised when a UI component payload fails validation."""

    def __init__(self, message: str, code: str | None = None) -> None:
        """Store the message and an optional machine-readable code."""
        super().__init__(message)
        self.code = code


def validate_component_payload(
    component: str,
    props: Mapping[str, Any] | None,
    registry: ComponentRegistry,
    *,
    allowlist: set[str] | None = None,
    limits: ValidationLimits | None = None,
    tool_context: MutableMapping[str, Any] | None = None,
    depth: int = 0,
    count_bytes: bool = True,
) -> None:
    """Validate a component payload against the registry and limits."""
    definition = registry.get(component)
    if definition is None:
        msg = f"Unknown component '{component}'."
        raise RichOutputValidationError(msg, code="unknown_component")

    allowed = allowlist or set()
    if allowed and component not in allowed:
        msg = f"Component '{component}' is not allowed."
        raise RichOutputValidationError(
            msg,
            code="component_not_allowed",
        )

    props_payload: Mapping[str, Any] = props or {}

    if limits is None:
        limits = ValidationLimits(max_payload_bytes=0, max_total_bytes=0)

    if count_bytes and limits.max_payload_bytes:
        size_bytes = _estimate_payload_bytes(
            {"component": component, "props": props_payload}
        )
        if size_bytes > limits.max_payload_bytes:
            msg = (
                "Component payload exceeds max_payload_bytes "
                f"({size_bytes} > {limits.max_payload_bytes})."
            )
            raise RichOutputValidationError(
                msg,
                code="payload_too_large",
            )
        _consume_total_budget(tool_context, size_bytes, limits.max_total_bytes)

    if isinstance(definition.props_schema, Mapping):
        validator = Draft7Validator(definition.props_schema)
        errors = sorted(
            validator.iter_errors(props_payload), key=lambda err: err.path
        )
        if errors:
            message = errors[0].message
            msg = f"Invalid props for '{component}': {message}"
            raise RichOutputValidationError(
                msg,
                code="schema_invalid",
            )

    if depth >= limits.max_depth:
        return

    for nested_component, nested_props in _iter_nested_components(
        component, props_payload
    ):
        validate_component_payload(
            nested_component,
            nested_props,
            registry,
            allowlist=allowlist,
            limits=limits,
            tool_context=tool_context,
            depth=depth + 1,
            count_bytes=False,
        )


def _consume_total_budget(
    tool_context: MutableMapping[str, Any] | None,
    size_bytes: int,
    max_total_bytes: int,
) -> None:
    if not tool_context or not max_total_bytes:
        return
    used = int(tool_context.get("_rich_output_bytes", 0))
    updated = used + size_bytes
    if updated > max_total_bytes:
        msg = (
            f"Component payload budget exceeded ({updated} > "
            f"{max_total_bytes})."
        )
        raise RichOutputValidationError(
            msg,
            code="budget_exceeded",
        )
    tool_context["_rich_output_bytes"] = updated


def _estimate_payload_bytes(payload: Mapping[str, Any]) -> int:
    try:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except Exception:
        encoded = str(payload).encode("utf-8")
    return len(encoded)


def _iter_nested_components(
    component: str, props: Mapping[str, Any]
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if component == "report":
        yield from _iter_report_sections(props.get("sections"))
    elif component == "grid":
        yield from _iter_component_items(props.get("items"))
    elif component == "tabs":
        yield from _iter_tab_items(props.get("tabs"))
    elif component == "accordion":
        yield from _iter_accordion_items(props.get("items"))


def _iter_report_sections(
    sections: object,
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(sections, list):
        return
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        components = section.get("components")
        yield from _iter_component_items(components)
        subsections = section.get("subsections")
        if isinstance(subsections, list):
            yield from _iter_report_sections(subsections)


def _iter_component_items(
    items: object,
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = item.get("component")
        props = item.get("props")
        if isinstance(name, str) and isinstance(props, Mapping):
            yield name, props


def _iter_tab_items(
    items: object,
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = item.get("component")
        props = item.get("props")
        if isinstance(name, str) and isinstance(props, Mapping):
            yield name, props


def _iter_accordion_items(
    items: object,
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = item.get("component")
        props = item.get("props")
        if isinstance(name, str) and isinstance(props, Mapping):
            yield name, props


__all__ = [
    "RichOutputValidationError",
    "ValidationLimits",
    "validate_component_payload",
]
