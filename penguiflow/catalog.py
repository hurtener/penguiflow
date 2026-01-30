"""Tool catalog helpers for the planner."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, TypeAlias, cast

from pydantic import BaseModel, model_validator

from .node import Node
from .registry import ModelRegistry

SideEffect: TypeAlias = Literal["pure", "read", "write", "external", "stateful"]


class ToolLoadingMode(str, Enum):
    ALWAYS = "always"
    DEFERRED = "deferred"


class ToolInputExample(BaseModel):
    args: dict[str, Any]
    description: str | None = None
    tags: list[str] = []

    @model_validator(mode="after")
    def _validate_args_and_tags(self) -> ToolInputExample:
        try:
            json.dumps(self.args, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("ToolInputExample.args must be JSON-serializable") from exc
        if self.tags:
            deduped: list[str] = []
            seen: set[str] = set()
            for tag in self.tags:
                tag_value = str(tag)
                if not tag_value or tag_value in seen:
                    continue
                seen.add(tag_value)
                deduped.append(tag_value)
            self.tags = deduped
        return self


def _coerce_examples(
    examples: Sequence[ToolInputExample | Mapping[str, Any]] | Mapping[str, Any] | None,
) -> tuple[ToolInputExample, ...]:
    if not examples:
        return ()
    if isinstance(examples, Mapping):
        examples = [examples]
    if not isinstance(examples, Sequence) or isinstance(examples, (str, bytes)):
        raise TypeError("Tool examples must be a sequence or mapping")
    parsed: list[ToolInputExample] = []
    for example in examples:
        if isinstance(example, ToolInputExample):
            parsed.append(example)
        elif isinstance(example, Mapping):
            parsed.append(ToolInputExample.model_validate(example))
        else:
            raise TypeError("Tool examples must be mappings or ToolInputExample instances")
    return tuple(parsed)


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
    loading_mode: ToolLoadingMode = ToolLoadingMode.ALWAYS
    examples: Sequence[ToolInputExample] = field(default_factory=tuple)

    def examples_payload(self) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        examples_source: Sequence[ToolInputExample | Mapping[str, Any]] | Mapping[str, Any] | None = self.examples
        if not examples_source and "examples" in self.extra:
            examples_source = self.extra.get("examples")
        for example in _coerce_examples(examples_source):
            data = example.model_dump()
            try:
                json.dumps(data, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            payload.append(data)
        return payload

    def to_tool_record(self) -> dict[str, Any]:
        """Convert the spec to a serialisable record for prompting."""
        safe_extra: dict[str, Any] = {}
        for key, value in self.extra.items():
            if key == "examples":
                continue
            if callable(value):
                continue
            try:
                json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            safe_extra[key] = value

        return {
            "name": self.name,
            "desc": self.desc,
            "side_effects": self.side_effects,
            "loading_mode": self.loading_mode.value,
            "tags": list(self.tags),
            "auth_scopes": list(self.auth_scopes),
            "cost_hint": self.cost_hint,
            "latency_hint_ms": self.latency_hint_ms,
            "safety_notes": self.safety_notes,
            "args_schema": self.args_model.model_json_schema(),
            "out_schema": self.out_model.model_json_schema(),
            "extra": safe_extra,
            "examples": self.examples_payload(),
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
    arg_validation: Mapping[str, Any] | None = None,
    arg_validator: Callable[..., Any] | None = None,
    extra: Mapping[str, Any] | None = None,
    loading_mode: ToolLoadingMode | str | None = None,
    examples: Sequence[ToolInputExample | Mapping[str, Any]] | Mapping[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Annotate a node function with catalog metadata."""

    extra_payload = dict(extra) if extra else {}
    if arg_validation is not None:
        extra_payload["arg_validation"] = dict(arg_validation)
    if arg_validator is not None:
        extra_payload["arg_validator"] = arg_validator

    if isinstance(loading_mode, str):
        loading_mode = ToolLoadingMode(loading_mode)

    payload: dict[str, Any] = {
        "desc": desc,
        "side_effects": side_effects,
        "tags": _normalise_sequence(tags),
        "auth_scopes": _normalise_sequence(auth_scopes),
        "cost_hint": cost_hint,
        "latency_hint_ms": latency_hint_ms,
        "safety_notes": safety_notes,
        "extra": extra_payload,
        "loading_mode": loading_mode,
        "examples": examples,
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
            "loading_mode": None,
            "examples": (),
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
        "loading_mode": raw.get("loading_mode"),
        "examples": raw.get("examples", ()),
    }


def build_catalog(
    nodes: Sequence[Node],
    registry: ModelRegistry,
    *,
    default_loading_mode: ToolLoadingMode | str | None = None,
) -> list[NodeSpec]:
    """Derive :class:`NodeSpec` objects from runtime nodes."""
    if isinstance(default_loading_mode, str):
        default_loading_mode = ToolLoadingMode(default_loading_mode)
    specs: list[NodeSpec] = []
    for node in nodes:
        node_name = node.name or node.func.__name__
        in_model, out_model = registry.models(node_name)
        metadata = _load_metadata(node.func)
        loading_mode = metadata.get("loading_mode")
        if isinstance(loading_mode, str):
            loading_mode = ToolLoadingMode(loading_mode)
        if loading_mode is None:
            loading_mode = default_loading_mode or ToolLoadingMode.ALWAYS
        extra_payload = dict(metadata["extra"])
        examples_raw = None
        if "examples" in extra_payload:
            examples_raw = extra_payload.pop("examples")
        else:
            examples_raw = metadata.get("examples")
        examples = _coerce_examples(examples_raw)
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
                extra=extra_payload,
                loading_mode=loading_mode,
                examples=examples,
            )
        )
    return specs


__all__ = [
    "NodeSpec",
    "SideEffect",
    "ToolInputExample",
    "ToolLoadingMode",
    "build_catalog",
    "tool",
]
