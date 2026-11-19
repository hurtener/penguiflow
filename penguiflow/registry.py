"""Model registry for PenguiFlow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic.type_adapter import TypeAdapter

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(slots=True)
class RegistryEntry:
    in_adapter: TypeAdapter[Any]
    out_adapter: TypeAdapter[Any]
    in_model: type[BaseModel]
    out_model: type[BaseModel]


def _is_model_class(value: object) -> bool:
    """Return whether ``value`` is a pydantic model class."""
    return isinstance(value, type) and issubclass(value, BaseModel)


class ModelRegistry:
    """Stores per-node type adapters for validation."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._entries: dict[str, RegistryEntry] = {}

    def register(
        self,
        node_name: str,
        in_model: type[BaseModel],
        out_model: type[BaseModel],
    ) -> None:
        """Register the input and output models for ``node_name``."""
        if node_name in self._entries:
            msg = f"Node '{node_name}' already registered"
            raise ValueError(msg)
        if not (_is_model_class(in_model) and _is_model_class(out_model)):
            msg = "Models must inherit from pydantic.BaseModel"
            raise TypeError(msg)
        self._entries[node_name] = RegistryEntry(
            TypeAdapter(in_model),
            TypeAdapter(out_model),
            in_model,
            out_model,
        )

    def has(self, node_name: str) -> bool:
        """Check if a node is already registered."""
        return node_name in self._entries

    def adapters(
        self, node_name: str
    ) -> tuple[TypeAdapter[Any], TypeAdapter[Any]]:
        """Return the input and output type adapters for ``node_name``."""
        try:
            entry = self._entries[node_name]
        except KeyError as exc:
            msg = f"Node '{node_name}' not registered"
            raise KeyError(msg) from exc
        return entry.in_adapter, entry.out_adapter

    def models(
        self, node_name: str
    ) -> tuple[type[BaseModel], type[BaseModel]]:
        """Return the registered models for ``node_name``.

        Raises:
            KeyError: If the node has not been registered.
        """
        try:
            entry = self._entries[node_name]
        except KeyError as exc:
            msg = f"Node '{node_name}' not registered"
            raise KeyError(msg) from exc
        return entry.in_model, entry.out_model


__all__ = ["ModelRegistry"]
