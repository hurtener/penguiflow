from __future__ import annotations

import pytest

from penguiflow.rich_output.registry import get_registry
from penguiflow.rich_output.validate import (
    RichOutputValidationError,
    ValidationLimits,
    validate_component_payload,
    validate_interaction_result,
)


def test_validate_component_payload_accepts_valid_props() -> None:
    registry = get_registry()
    validate_component_payload(
        "markdown",
        {"content": "Hello"},
        registry,
        allowlist={"markdown"},
        limits=ValidationLimits(max_payload_bytes=1000, max_total_bytes=2000),
        tool_context={},
    )


def test_validate_component_payload_rejects_unknown() -> None:
    registry = get_registry()
    with pytest.raises(RichOutputValidationError):
        validate_component_payload(
            "nope",
            {},
            registry,
            allowlist=None,
            limits=ValidationLimits(max_payload_bytes=1000, max_total_bytes=2000),
            tool_context={},
        )


def test_validate_component_payload_rejects_disallowed() -> None:
    registry = get_registry()
    with pytest.raises(RichOutputValidationError):
        validate_component_payload(
            "markdown",
            {"content": "Hello"},
            registry,
            allowlist={"json"},
            limits=ValidationLimits(max_payload_bytes=1000, max_total_bytes=2000),
            tool_context={},
        )


def test_validate_component_payload_enforces_size() -> None:
    registry = get_registry()
    with pytest.raises(RichOutputValidationError):
        validate_component_payload(
            "markdown",
            {"content": "x" * 200},
            registry,
            allowlist={"markdown"},
            limits=ValidationLimits(max_payload_bytes=20, max_total_bytes=2000),
            tool_context={},
        )


def test_validate_component_payload_recurses_into_grid() -> None:
    registry = get_registry()
    validate_component_payload(
        "grid",
        {
            "columns": 2,
            "items": [
                {"component": "markdown", "props": {"content": "Hi"}},
            ],
        },
        registry,
        allowlist={"grid", "markdown"},
        limits=ValidationLimits(max_payload_bytes=1000, max_total_bytes=2000),
        tool_context={},
    )


def test_validate_interaction_result_shapes() -> None:
    validate_interaction_result("confirm", True)
    validate_interaction_result("select_option", "line")
    validate_interaction_result("select_option", ["line", "bar"])
    validate_interaction_result("select_option", {"selection": None, "cancelled": True})
    validate_interaction_result("form", {"field": "value"})

    with pytest.raises(RichOutputValidationError):
        validate_interaction_result("confirm", "yes")
