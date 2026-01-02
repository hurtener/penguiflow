"""Component registry loader for rich output UI artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any


class RegistryError(RuntimeError):
    """Raised when the component registry cannot be loaded or parsed."""


@dataclass(frozen=True)
class ComponentDefinition:
    """Definition for a single UI component."""

    name: str
    description: str
    props_schema: dict[str, Any]
    interactive: bool
    category: str
    tags: tuple[str, ...]
    example: dict[str, Any] | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ComponentDefinition:
        try:
            name = str(payload["name"])
            description = str(payload.get("description", ""))
            props_schema = dict(payload.get("propsSchema", {}))
            interactive = bool(payload.get("interactive", False))
            category = str(payload.get("category", ""))
            tags = tuple(payload.get("tags", []) or [])
            example = payload.get("example")
        except Exception as exc:  # pragma: no cover - defensive guard
            raise RegistryError(f"Invalid component definition: {exc}") from exc

        return cls(
            name=name,
            description=description,
            props_schema=props_schema,
            interactive=interactive,
            category=category,
            tags=tags,
            example=dict(example) if isinstance(example, Mapping) else None,
        )


@dataclass(frozen=True)
class ComponentRegistry:
    """Loaded registry of UI component definitions."""

    version: str
    components: dict[str, ComponentDefinition]
    raw: dict[str, Any]

    def get(self, name: str) -> ComponentDefinition | None:
        return self.components.get(name)

    def allowlist(self, allowed: set[str] | None) -> dict[str, ComponentDefinition]:
        if not allowed:
            return dict(self.components)
        return {name: comp for name, comp in self.components.items() if name in allowed}


def _load_registry_data(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive guard
        raise RegistryError(f"Failed to load registry JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistryError("Registry JSON must be an object")
    if "components" not in payload or not isinstance(payload["components"], dict):
        raise RegistryError("Registry JSON must include a components map")
    return payload


def _default_registry_path() -> Path:
    try:
        return resources.files("penguiflow.rich_output").joinpath("registry.json")  # type: ignore[return-value]
    except Exception as exc:  # pragma: no cover - defensive guard
        raise RegistryError(f"Cannot locate registry.json: {exc}") from exc


def load_registry(path: Path | None = None) -> ComponentRegistry:
    """Load the component registry from disk."""
    registry_path = path or _default_registry_path()
    payload = _load_registry_data(Path(registry_path))
    components_raw = payload.get("components", {})
    components: dict[str, ComponentDefinition] = {}
    for name, definition in components_raw.items():
        if not isinstance(definition, Mapping):
            raise RegistryError(f"Component '{name}' must be an object")
        component = ComponentDefinition.from_payload(definition)
        components[name] = component
    version = str(payload.get("version", "unknown"))
    return ComponentRegistry(version=version, components=components, raw=payload)


@lru_cache(maxsize=1)
def get_registry(path: Path | None = None) -> ComponentRegistry:
    """Load and cache the registry for reuse."""
    return load_registry(path)


__all__ = ["ComponentDefinition", "ComponentRegistry", "RegistryError", "get_registry", "load_registry"]
