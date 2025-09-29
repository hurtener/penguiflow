"""Policy helpers for dynamic routing decisions."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, cast

from .node import Node

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from .core import Context
else:  # pragma: no cover - runtime fallback
    class Context:  # type: ignore[too-many-ancestors]
        """Placeholder context type for runtime annotations."""

        pass

RoutingDecisionType: TypeAlias = None | Node | str | Sequence[Node | str]


@dataclass(slots=True)
class RoutingRequest:
    """Information provided to routing policies."""

    message: Any
    context: Context
    node: Node
    proposed: tuple[Node, ...]
    trace_id: str | None

    @property
    def node_name(self) -> str:
        return self.node.name or self.node.node_id

    @property
    def proposed_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for candidate in self.proposed:
            names.append(candidate.name or candidate.node_id)
        return tuple(names)


class RoutingPolicy(Protocol):
    """Protocol for routing policies used by router nodes."""

    def select(
        self, request: RoutingRequest
    ) -> RoutingDecisionType | Awaitable[RoutingDecisionType]:
        """Return the desired routing targets for *request*."""


PolicyCallable = Callable[
    [RoutingRequest], RoutingDecisionType | Awaitable[RoutingDecisionType]
]
PolicyLike = RoutingPolicy | PolicyCallable


async def evaluate_policy(
    policy: PolicyLike,
    request: RoutingRequest,
) -> RoutingDecisionType:
    """Evaluate *policy* for the given *request* supporting sync/async returns."""

    if hasattr(policy, "select"):
        selector = cast(RoutingPolicy, policy).select
        candidate = selector(request)
    else:
        candidate = cast(PolicyCallable, policy)(request)

    if inspect.isawaitable(candidate):
        return await candidate
    return candidate


KeyFn = Callable[[RoutingRequest], str | None]


class DictRoutingPolicy:
    """Routing policy driven by a mapping loaded from config."""

    def __init__(
        self,
        mapping: Mapping[str, RoutingDecisionType],
        *,
        default: RoutingDecisionType = None,
        key_getter: KeyFn | None = None,
    ) -> None:
        self._mapping: dict[str, RoutingDecisionType] = dict(mapping)
        self._default = default
        self._key_getter = key_getter or (lambda request: request.trace_id)

    def select(self, request: RoutingRequest) -> RoutingDecisionType:
        key = self._key_getter(request)
        if key is None:
            return self._default
        return self._mapping.get(key, self._default)

    def update_mapping(self, mapping: Mapping[str, RoutingDecisionType]) -> None:
        self._mapping = dict(mapping)

    def set_default(self, decision: RoutingDecisionType) -> None:
        self._default = decision

    @classmethod
    def from_json(cls, payload: str, **kwargs: Any) -> DictRoutingPolicy:
        data = json.loads(payload)
        if not isinstance(data, Mapping):
            raise TypeError("JSON payload must decode to a mapping")
        return cls(data, **kwargs)

    @classmethod
    def from_json_file(cls, path: str, **kwargs: Any) -> DictRoutingPolicy:
        with open(path, encoding="utf-8") as fh:
            return cls.from_json(fh.read(), **kwargs)

    @classmethod
    def from_env(
        cls,
        env_var: str,
        *,
        loader: Callable[[str], Mapping[str, RoutingDecisionType]] | None = None,
        default: RoutingDecisionType = None,
        key_getter: KeyFn | None = None,
    ) -> DictRoutingPolicy:
        raw = os.getenv(env_var)
        if raw is None:
            raise KeyError(f"Environment variable '{env_var}' not set")
        if loader is None:
            data = json.loads(raw)
        else:
            data = loader(raw)
        if not isinstance(data, Mapping):
            raise TypeError("Policy loader must return a mapping")
        return cls(data, default=default, key_getter=key_getter)


__all__ = [
    "DictRoutingPolicy",
    "PolicyCallable",
    "PolicyLike",
    "RoutingDecisionType",
    "RoutingPolicy",
    "RoutingRequest",
    "evaluate_policy",
]
