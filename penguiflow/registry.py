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


class ModelRegistry:
    """Stores per-node type adapters for validation."""

    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}

    def register(
        self,
        node_name: str,
        in_model: type[BaseModel],
        out_model: type[BaseModel],
    ) -> None:
        if node_name in self._entries:
            raise ValueError(f"Node '{node_name}' already registered")
        if not issubclass(in_model, BaseModel) or not issubclass(out_model, BaseModel):
            raise TypeError("Models must inherit from pydantic.BaseModel")
        self._entries[node_name] = RegistryEntry(
            TypeAdapter(in_model),
            TypeAdapter(out_model),
        )

    def adapters(self, node_name: str) -> tuple[TypeAdapter[Any], TypeAdapter[Any]]:
        try:
            entry = self._entries[node_name]
        except KeyError as exc:
            raise KeyError(f"Node '{node_name}' not registered") from exc
        return entry.in_adapter, entry.out_adapter


__all__ = ["ModelRegistry"]
