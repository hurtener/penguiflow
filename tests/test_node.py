"""Tests for Node validation and error handling."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from penguiflow import ModelRegistry, Node, NodePolicy
from penguiflow.node import Node as NodeClass


def test_node_with_sync_function_raises_error() -> None:
    """Node should reject non-async functions."""
    def sync_func(msg: str, _ctx) -> str:
        return msg

    with pytest.raises(TypeError, match="must be declared with async def"):
        Node(sync_func, name="sync")


def test_node_with_wrong_parameter_count() -> None:
    """Node should require exactly 2 parameters."""
    async def no_params() -> str:
        return "test"

    with pytest.raises(ValueError, match="exactly two parameters"):
        Node(no_params, name="no_params")

    async def one_param(msg: str) -> str:
        return msg

    with pytest.raises(ValueError, match="exactly two parameters"):
        Node(one_param, name="one_param")

    async def three_params(msg: str, _ctx, _extra) -> str:
        return msg

    with pytest.raises(ValueError, match="exactly two parameters"):
        Node(three_params, name="three_params")


def test_node_with_invalid_parameter_kind() -> None:
    """Context parameter must be positional."""
    async def keyword_only_ctx(msg: str, *, _ctx) -> str:
        return msg

    with pytest.raises(ValueError, match="Context parameter must be positional"):
        Node(keyword_only_ctx, name="keyword_ctx")


def test_node_policy_invalid_validate_option() -> None:
    """NodePolicy should reject invalid validate options."""
    with pytest.raises(ValueError, match="validate must be one of"):
        NodePolicy(validate="invalid")

    # Valid options should not raise
    NodePolicy(validate="both")
    NodePolicy(validate="in")
    NodePolicy(validate="out")
    NodePolicy(validate="none")


@pytest.mark.asyncio
async def test_node_validation_failure_with_registry() -> None:
    """Node should fail validation when input doesn't match registered model."""

    class InputModel(BaseModel):
        value: int

    class OutputModel(BaseModel):
        result: str

    async def processor(msg: InputModel, _ctx) -> OutputModel:
        return OutputModel(result=str(msg.value))

    node = NodeClass(
        processor,
        name="processor",
        policy=NodePolicy(validate="both")
    )

    registry = ModelRegistry()
    registry.register("processor", InputModel, OutputModel)

    # Should fail validation with wrong input type
    from penguiflow.core import Context, Endpoint
    ctx = Context(Endpoint("test"))

    with pytest.raises((ValueError, TypeError)):  # Will raise validation error
        await node.invoke("not_an_int", ctx, registry=registry)

    # Should succeed with correct input
    valid_input = InputModel(value=42)
    result = await node.invoke(valid_input, ctx, registry=registry)
    assert isinstance(result, OutputModel)
    assert result.result == "42"
