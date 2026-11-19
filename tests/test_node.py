"""Tests for Node validation and error handling."""

from __future__ import annotations

import pytest

from penguiflow import Node, NodePolicy


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
