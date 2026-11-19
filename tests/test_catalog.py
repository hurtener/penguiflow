"""Tests for catalog."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.registry import ModelRegistry


class EchoArgs(BaseModel):
    """Arguments of the echo tool."""

    message: str


class EchoOut(BaseModel):
    """Output of the echo tool."""

    echoed: str


@tool(desc="Echo a message", tags=["utility", "utility"], side_effects="read")
async def echo(args: EchoArgs, _ctx: object) -> EchoOut:
    """Echo helper."""
    return EchoOut(echoed=args.message)


async def describe(args: EchoArgs, _ctx: object) -> EchoOut:
    """Describe the payload."""
    return EchoOut(echoed=f"desc:{args.message}")


@tool(
    desc="Echo with examples",
    examples=[
        {
            "args": {"message": "hello"},
            "description": "Basic echo",
            "tags": ["minimal"],
        },
        {
            "args": {"message": "hi"},
            "description": "Short input",
            "tags": ["common"],
        },
    ],
)
async def echo_with_examples(args: EchoArgs, _ctx: object) -> EchoOut:
    """Echo the input; the spec carries examples."""
    return EchoOut(echoed=args.message)


@pytest.fixture()
def registry() -> ModelRegistry:
    """Provide an empty model registry."""
    reg = ModelRegistry()
    reg.register("echo", EchoArgs, EchoOut)
    reg.register("describe", EchoArgs, EchoOut)
    return reg


def test_build_catalog_uses_metadata(registry: ModelRegistry) -> None:
    """Build catalog uses metadata."""
    node = Node(echo, name="echo")
    specs = build_catalog([node], registry)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "echo"
    assert spec.desc == "Echo a message"
    assert spec.side_effects == "read"
    assert spec.tags == ("utility",)
    record = spec.to_tool_record()
    assert record["args_schema"]["title"] == "EchoArgs"
    assert json.loads(json.dumps(record["out_schema"]))["title"] == "EchoOut"


def test_build_catalog_falls_back_to_docstring(
    registry: ModelRegistry,
) -> None:
    """Build catalog falls back to docstring."""
    node = Node(describe, name="describe")
    specs = build_catalog([node], registry)
    spec = specs[0]
    assert spec.desc == "Describe the payload."
    assert spec.side_effects == "pure"


def test_build_catalog_includes_tool_examples() -> None:
    """Build catalog includes tool examples."""
    registry = ModelRegistry()
    registry.register("echo_with_examples", EchoArgs, EchoOut)
    node = Node(echo_with_examples, name="echo_with_examples")
    specs = build_catalog([node], registry)
    spec = specs[0]
    record = spec.to_tool_record()
    assert record["examples"][0]["args"] == {"message": "hello"}
    assert record["examples"][0]["tags"] == ["minimal"]
