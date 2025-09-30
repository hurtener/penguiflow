"""Tool catalog helpers for the planner."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel

from .node import Node
from .registry import ModelRegistry

SideEffect = Literal["pure", "read", "write", "external", "stateful"]


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """Structured metadata describing a planner-discoverable node."""

    node: Node
    name: str
    desc: str
    args_model: type[BaseModel]
    out_model: type[BaseModel]
    side_effects: SideEffect = "pure"
    tags: Sequence[str] = field(default_factory=tuple)
    auth_scopes: Sequence[str] = field(default_factory=tuple)
    cost_hint: str | None = None
    latency_hint_ms: int | None = None
    safety_notes: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_tool_record(self) -> dict[str, Any]:
        """Convert the spec to a serialisable record for prompting."""

        return {
            "name": self.name,
            "desc": self.desc,
            "side_effects": self.side_effects,
            "tags": list(self.tags),
            "auth_scopes": list(self.auth_scopes),
            "cost_hint": self.cost_hint,
            "latency_hint_ms": self.latency_hint_ms,
            "safety_notes": self.safety_notes,
            "args_schema": self.args_model.model_json_schema(),
            "out_schema": self.out_model.model_json_schema(),
            "extra": dict(self.extra),
        }


def _normalise_sequence(value: Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(dict.fromkeys(value))


def tool(
    *,
    desc: str | None = None,
    side_effects: SideEffect = "pure",
    tags: Sequence[str] | None = None,
    auth_scopes: Sequence[str] | None = None,
    cost_hint: str | None = None,
    latency_hint_ms: int | None = None,
    safety_notes: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Annotate a node function with catalog metadata."""

    payload: dict[str, Any] = {
        "desc": desc,
        "side_effects": side_effects,
        "tags": _normalise_sequence(tags),
        "auth_scopes": _normalise_sequence(auth_scopes),
        "cost_hint": cost_hint,
        "latency_hint_ms": latency_hint_ms,
        "safety_notes": safety_notes,
        "extra": dict(extra) if extra else {},
    }

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func_ref = cast(Any, func)
        func_ref.__penguiflow_tool__ = payload
        return func

    return decorator


def _load_metadata(func: Callable[..., Any]) -> dict[str, Any]:
    raw = getattr(func, "__penguiflow_tool__", None)
    if not raw:
        return {
            "desc": inspect.getdoc(func) or func.__name__,
            "side_effects": "pure",
            "tags": (),
            "auth_scopes": (),
            "cost_hint": None,
            "latency_hint_ms": None,
            "safety_notes": None,
            "extra": {},
        }
    return {
        "desc": raw.get("desc") or inspect.getdoc(func) or func.__name__,
        "side_effects": raw.get("side_effects", "pure"),
        "tags": tuple(raw.get("tags", ())),
        "auth_scopes": tuple(raw.get("auth_scopes", ())),
        "cost_hint": raw.get("cost_hint"),
        "latency_hint_ms": raw.get("latency_hint_ms"),
        "safety_notes": raw.get("safety_notes"),
        "extra": dict(raw.get("extra", {})),
    }


def build_catalog(
    nodes: Sequence[Node],
    registry: ModelRegistry,
) -> list[NodeSpec]:
    """Derive :class:`NodeSpec` objects from runtime nodes."""

    specs: list[NodeSpec] = []
    for node in nodes:
        node_name = node.name or node.func.__name__
        in_model, out_model = registry.models(node_name)
        metadata = _load_metadata(node.func)
        specs.append(
            NodeSpec(
                node=node,
                name=node_name,
                desc=metadata["desc"],
                args_model=in_model,
                out_model=out_model,
                side_effects=metadata["side_effects"],
                tags=metadata["tags"],
                auth_scopes=metadata["auth_scopes"],
                cost_hint=metadata["cost_hint"],
                latency_hint_ms=metadata["latency_hint_ms"],
                safety_notes=metadata["safety_notes"],
                extra=metadata["extra"],
            )
        )
    return specs


__all__ = ["NodeSpec", "SideEffect", "build_catalog", "tool"]
