"""Validation helpers for rich output component payloads."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft7Validator

from .registry import ComponentRegistry


@dataclass(frozen=True)
class ValidationLimits:
    max_payload_bytes: int
    max_total_bytes: int
    max_depth: int = 6


class RichOutputValidationError(ValueError):
    """Raised when a UI component payload fails validation."""

    def __init__(self, message: str, code: str | None = None) -> None:
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
        raise RichOutputValidationError(f"Unknown component '{component}'.", code="unknown_component")

    allowed = allowlist or set()
    if allowed and component not in allowed:
        raise RichOutputValidationError(
            f"Component '{component}' is not allowed.",
            code="component_not_allowed",
        )

    props_payload: Mapping[str, Any] = props or {}

    if limits is None:
        limits = ValidationLimits(max_payload_bytes=0, max_total_bytes=0)

    if count_bytes and limits.max_payload_bytes:
        size_bytes = _estimate_payload_bytes({"component": component, "props": props_payload})
        if size_bytes > limits.max_payload_bytes:
            raise RichOutputValidationError(
                f"Component payload exceeds max_payload_bytes ({size_bytes} > {limits.max_payload_bytes}).",
                code="payload_too_large",
            )
        _consume_total_budget(tool_context, size_bytes, limits.max_total_bytes)

    if isinstance(definition.props_schema, Mapping):
        validator = Draft7Validator(definition.props_schema)
        errors = sorted(validator.iter_errors(props_payload), key=lambda err: err.path)
        if errors:
            message = errors[0].message
            raise RichOutputValidationError(f"Invalid props for '{component}': {message}", code="schema_invalid")

    if depth >= limits.max_depth:
        return

    for nested_component, nested_props in _iter_nested_components(component, props_payload):
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
        raise RichOutputValidationError(
            f"Component payload budget exceeded ({updated} > {max_total_bytes}).",
            code="budget_exceeded",
        )
    tool_context["_rich_output_bytes"] = updated


def _estimate_payload_bytes(payload: Mapping[str, Any]) -> int:
    try:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except Exception:
        encoded = str(payload).encode("utf-8")
    return len(encoded)


def _iter_nested_components(component: str, props: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if component == "report":
        yield from _iter_report_sections(props.get("sections"))
    elif component == "grid":
        yield from _iter_component_items(props.get("items"))
    elif component == "tabs":
        yield from _iter_tab_items(props.get("tabs"))
    elif component == "accordion":
        yield from _iter_accordion_items(props.get("items"))


def _iter_report_sections(sections: Any) -> Iterable[tuple[str, Mapping[str, Any]]]:
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


def _iter_component_items(items: Any) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = item.get("component")
        props = item.get("props")
        if isinstance(name, str) and isinstance(props, Mapping):
            yield name, props


def _iter_tab_items(items: Any) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = item.get("component")
        props = item.get("props")
        if isinstance(name, str) and isinstance(props, Mapping):
            yield name, props


def _iter_accordion_items(items: Any) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = item.get("component")
        props = item.get("props")
        if isinstance(name, str) and isinstance(props, Mapping):
            yield name, props


def validate_interaction_result(component: str, result: Any) -> None:
    """Validate interactive component result payloads."""

    if component == "confirm":
        if isinstance(result, Mapping):
            value = result.get("confirmed")
            if isinstance(value, bool):
                return
        if isinstance(result, bool):
            return
        raise RichOutputValidationError("Confirm result must be boolean.", code="invalid_result")

    if component == "select_option":
        if isinstance(result, Mapping):
            if result.get("cancelled") is True or result.get("_cancelled") is True:
                return
            value = result.get("selection")
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                return
            if isinstance(value, str):
                return
        if isinstance(result, list) and all(isinstance(item, str) for item in result):
            return
        if isinstance(result, str):
            return
        raise RichOutputValidationError(
            "Select option result must be string or list of strings.",
            code="invalid_result",
        )

    if component == "form":
        if result is None:
            return
        if isinstance(result, Mapping):
            return
        raise RichOutputValidationError("Form result must be an object.", code="invalid_result")


__all__ = [
    "RichOutputValidationError",
    "ValidationLimits",
    "validate_component_payload",
    "validate_interaction_result",
]
