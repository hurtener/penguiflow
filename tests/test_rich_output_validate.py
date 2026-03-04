from __future__ import annotations

import pytest

from penguiflow.rich_output.registry import get_registry
from penguiflow.rich_output.validate import (
    RichOutputValidationError,
    ValidationLimits,
    _estimate_payload_bytes,
    _iter_accordion_items,
    _iter_component_items,
    _iter_report_sections,
    _iter_tab_items,
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


def test_validate_component_payload_budget_and_default_limits() -> None:
    registry = get_registry()
    with pytest.raises(RichOutputValidationError, match="budget exceeded"):
        validate_component_payload(
            "markdown",
            {"content": "abc"},
            registry,
            allowlist={"markdown"},
            limits=ValidationLimits(max_payload_bytes=1000, max_total_bytes=5),
            tool_context={"_rich_output_bytes": 5},
        )

    validate_component_payload(
        "markdown",
        {"content": "abc"},
        registry,
        allowlist={"markdown"},
        limits=None,
        tool_context={},
    )


def test_validate_component_payload_depth_limit_short_circuits() -> None:
    registry = get_registry()
    validate_component_payload(
        "report",
        {"sections": [{"components": [{"component": "nope", "props": {}}]}]},
        registry,
        allowlist={"report"},
        limits=ValidationLimits(max_payload_bytes=1000, max_total_bytes=2000, max_depth=1),
        tool_context={},
        depth=1,
    )


def test_estimate_payload_bytes_falls_back_to_string_encoding() -> None:
    payload = {"bad": {1, 2, 3}}
    size = _estimate_payload_bytes(payload)
    assert size > 0


def test_nested_component_iterators_cover_invalid_and_valid_shapes() -> None:
    assert list(_iter_report_sections("not-a-list")) == []
    assert list(_iter_component_items("not-a-list")) == []
    assert list(_iter_tab_items("not-a-list")) == []
    assert list(_iter_accordion_items("not-a-list")) == []

    report_sections = [
        "skip",
        {
            "components": [{"component": "markdown", "props": {"content": "Hi"}}, {"component": "markdown"}],
            "subsections": [{"components": [{"component": "json", "props": {"data": {"a": 1}}}]}],
        },
    ]
    tabs = [{"component": "markdown", "props": {"content": "A"}}, {"component": "markdown", "props": None}]
    accordion = [{"component": "json", "props": {"data": {"a": 1}}}, {"component": "json", "props": None}]

    assert list(_iter_report_sections(report_sections)) == [
        ("markdown", {"content": "Hi"}),
        ("json", {"data": {"a": 1}}),
    ]
    assert list(_iter_tab_items(tabs)) == [("markdown", {"content": "A"})]
    assert list(_iter_accordion_items(accordion)) == [("json", {"data": {"a": 1}})]


def test_validate_interaction_result_additional_paths() -> None:
    validate_interaction_result("confirm", {"confirmed": False})
    validate_interaction_result("select_option", {"selection": "line"})
    validate_interaction_result("select_option", {"selection": ["line", "bar"]})
    validate_interaction_result("form", None)

    with pytest.raises(RichOutputValidationError):
        validate_interaction_result("select_option", 123)
    with pytest.raises(RichOutputValidationError):
        validate_interaction_result("form", 123)
