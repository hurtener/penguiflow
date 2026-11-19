"""Tests for ModelRegistry validation."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from penguiflow.registry import ModelRegistry


class InputModel(BaseModel):
    """Input model used by the tests."""

    text: str


class OutputModel(BaseModel):
    """Output model used by the tests."""

    text: str
    length: int


def test_registry_validates_in_and_out() -> None:
    """Registry validates in and out."""
    registry = ModelRegistry()
    registry.register("worker", InputModel, OutputModel)
    adapter_in, adapter_out = registry.adapters("worker")

    parsed = adapter_in.validate_python({"text": "penguin"})
    assert isinstance(parsed, InputModel)
    result = adapter_out.validate_python(
        {"text": parsed.text, "length": len(parsed.text)}
    )
    assert isinstance(result, OutputModel)
    assert result.length == 7


def test_registry_rejects_invalid_input() -> None:
    """Registry rejects invalid input."""
    registry = ModelRegistry()
    registry.register("worker", InputModel, OutputModel)
    adapter_in, _ = registry.adapters("worker")

    with pytest.raises(ValidationError):
        adapter_in.validate_python({})


def test_registry_rejects_invalid_output() -> None:
    """Registry rejects invalid output."""
    registry = ModelRegistry()
    registry.register("worker", InputModel, OutputModel)
    _, adapter_out = registry.adapters("worker")

    with pytest.raises(ValidationError):
        adapter_out.validate_python({"text": "penguin"})


def test_registry_duplicate_registration_raises() -> None:
    """Registry duplicate registration raises."""
    registry = ModelRegistry()
    registry.register("dup", InputModel, OutputModel)
    with pytest.raises(ValueError):
        registry.register("dup", InputModel, OutputModel)


def test_registry_unknown_node_raises() -> None:
    """Registry unknown node raises."""
    registry = ModelRegistry()
    with pytest.raises(KeyError):
        registry.adapters("missing")
