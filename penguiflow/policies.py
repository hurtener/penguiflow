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
    """Information provided to routing policies.

    Attributes:
        message: The message payload being routed.
        context: The runtime ``Context`` in which routing is occurring.
        node: The router node making the routing decision.
        proposed: The candidate downstream nodes proposed by the router.
        trace_id: Identifier of the trace being routed, if known.
    """

    message: Any
    context: Context
    node: Node
    proposed: tuple[Node, ...]
    trace_id: str | None

    @property
    def node_name(self) -> str:
        """Return the router node's name, falling back to its node id."""

        return self.node.name or self.node.node_id

    @property
    def proposed_names(self) -> tuple[str, ...]:
        """Return the display names of the proposed candidate nodes.

        Returns:
            A tuple of names (or node ids when a candidate has no name), in the same
            order as ``proposed``.
        """

        names: list[str] = []
        for candidate in self.proposed:
            names.append(candidate.name or candidate.node_id)
        return tuple(names)


class RoutingPolicy(Protocol):
    """Protocol for routing policies used by router nodes.

    Implementations decide, for a given ``RoutingRequest``, which downstream node(s)
    a message should be routed to.
    """

    def select(self, request: RoutingRequest) -> RoutingDecisionType | Awaitable[RoutingDecisionType]:
        """Return the desired routing targets for *request*.

        Args:
            request: The routing request describing the message, node, and proposed
                candidates.

        Returns:
            ``None`` to accept the proposed routing, a single ``Node`` or node name, a
            sequence of nodes/names to route to, or an awaitable resolving to one of
            these.
        """


PolicyCallable = Callable[[RoutingRequest], RoutingDecisionType | Awaitable[RoutingDecisionType]]
PolicyLike = RoutingPolicy | PolicyCallable


async def evaluate_policy(
    policy: PolicyLike,
    request: RoutingRequest,
) -> RoutingDecisionType:
    """Evaluate *policy* for the given *request* supporting sync/async returns.

    Args:
        policy: Either a ``RoutingPolicy`` instance (with a ``select`` method) or a
            plain callable accepting a ``RoutingRequest``.
        request: The routing request to evaluate.

    Returns:
        The resolved routing decision (see ``RoutingDecisionType``), after awaiting it
        if the policy returned an awaitable.
    """

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
    """Routing policy driven by a mapping loaded from config.

    Selection uses ``key_getter`` (the trace id by default) to look up a routing
    decision in the configured mapping, falling back to ``default`` when the key is
    absent or unmapped.
    """

    def __init__(
        self,
        mapping: Mapping[str, RoutingDecisionType],
        *,
        default: RoutingDecisionType = None,
        key_getter: KeyFn | None = None,
    ) -> None:
        """Initialize the policy with a mapping and optional default/key function.

        Args:
            mapping: Mapping from lookup key to routing decision.
            default: Decision to return when the key is missing or unmapped.
            key_getter: Callable deriving the lookup key from a ``RoutingRequest``;
                defaults to using ``request.trace_id``.
        """
        self._mapping: dict[str, RoutingDecisionType] = dict(mapping)
        self._default = default
        self._key_getter = key_getter or (lambda request: request.trace_id)

    def select(self, request: RoutingRequest) -> RoutingDecisionType:
        """Return the routing decision configured for *request*'s key.

        Args:
            request: The routing request to evaluate.

        Returns:
            The mapped routing decision, or the configured default if the key is
            ``None`` or not present in the mapping.
        """
        key = self._key_getter(request)
        if key is None:
            return self._default
        return self._mapping.get(key, self._default)

    def update_mapping(self, mapping: Mapping[str, RoutingDecisionType]) -> None:
        """Replace the policy's mapping in place.

        Args:
            mapping: The new mapping from lookup key to routing decision.
        """
        self._mapping = dict(mapping)

    def set_default(self, decision: RoutingDecisionType) -> None:
        """Set the fallback decision used when a key is missing or unmapped.

        Args:
            decision: The new default routing decision.
        """
        self._default = decision

    @classmethod
    def from_json(cls, payload: str, **kwargs: Any) -> DictRoutingPolicy:
        """Build a policy from a JSON string that decodes to a mapping.

        Args:
            payload: JSON-encoded mapping of lookup key to routing decision.
            **kwargs: Additional keyword arguments forwarded to the constructor
                (e.g. ``default``, ``key_getter``).

        Returns:
            A new ``DictRoutingPolicy`` built from the decoded mapping.

        Raises:
            TypeError: If the decoded JSON payload is not a mapping.
        """
        data = json.loads(payload)
        if not isinstance(data, Mapping):
            raise TypeError("JSON payload must decode to a mapping")
        return cls(data, **kwargs)

    @classmethod
    def from_json_file(cls, path: str, **kwargs: Any) -> DictRoutingPolicy:
        """Build a policy from a JSON file that decodes to a mapping.

        Args:
            path: Path to a UTF-8 encoded JSON file.
            **kwargs: Additional keyword arguments forwarded to the constructor
                (e.g. ``default``, ``key_getter``).

        Returns:
            A new ``DictRoutingPolicy`` built from the file's decoded mapping.

        Raises:
            TypeError: If the decoded JSON payload is not a mapping.
        """
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
        """Build a policy from an environment variable.

        Args:
            env_var: Name of the environment variable holding the policy data.
            loader: Optional callable to parse the raw environment value into a
                mapping; defaults to ``json.loads``.
            default: Decision to return when the key is missing or unmapped.
            key_getter: Callable deriving the lookup key from a ``RoutingRequest``;
                defaults to using ``request.trace_id``.

        Returns:
            A new ``DictRoutingPolicy`` built from the parsed mapping.

        Raises:
            KeyError: If the environment variable is not set.
            TypeError: If the parsed data is not a mapping.
        """
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
