"""Tests for ModelRegistry and Node validation."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from penguiflow.node import Node, NodePolicy
from penguiflow.registry import ModelRegistry


class InputModel(BaseModel):
    text: str


class OutputModel(BaseModel):
    text: str
    length: int


@pytest.mark.asyncio
async def test_registry_validates_in_and_out() -> None:
    async def worker(msg: InputModel, ctx) -> OutputModel:
        return OutputModel(text=msg.text, length=len(msg.text))

    node = Node(worker, name="worker", policy=NodePolicy(validate="both"))
    registry = ModelRegistry()
    registry.register("worker", InputModel, OutputModel)

    result = await node.invoke({"text": "penguin"}, object(), registry=registry)
    assert isinstance(result, OutputModel)
    assert result.length == 7


@pytest.mark.asyncio
async def test_registry_rejects_invalid_input() -> None:
    async def worker(msg: InputModel, ctx) -> OutputModel:
        return OutputModel(text=msg.text, length=len(msg.text))

    node = Node(worker, name="worker", policy=NodePolicy(validate="in"))
    registry = ModelRegistry()
    registry.register("worker", InputModel, OutputModel)

    with pytest.raises(ValidationError):
        await node.invoke({}, object(), registry=registry)


@pytest.mark.asyncio
async def test_registry_rejects_invalid_output() -> None:
    async def worker(msg: InputModel, ctx) -> dict[str, str]:
        return {"text": msg.text}

    node = Node(worker, name="worker", policy=NodePolicy(validate="out"))
    registry = ModelRegistry()
    registry.register("worker", InputModel, OutputModel)

    with pytest.raises(ValidationError):
        await node.invoke(InputModel(text="penguin"), object(), registry=registry)


def test_registry_duplicate_registration_raises() -> None:
    registry = ModelRegistry()
    registry.register("dup", InputModel, OutputModel)
    with pytest.raises(ValueError):
        registry.register("dup", InputModel, OutputModel)


def test_registry_unknown_node_raises() -> None:
    registry = ModelRegistry()
    with pytest.raises(KeyError):
        registry.adapters("missing")
