"""Agent registry and route scoring for A2A router agents."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from penguiflow.catalog import NodeSpec, SideEffect
from penguiflow.node import Node, NodePolicy
from penguiflow.planner.context import ToolContext
from penguiflow.remote import RemoteTransport

from .planner_tools import A2AAgentToolset, ExecutionMode

TrustTier = Literal["low", "standard", "trusted", "critical"]
LatencyTier = Literal["interactive", "standard", "batch"]

_TRUST_SCORE = {"low": 0, "standard": 1, "trusted": 2, "critical": 3}
_LATENCY_SCORE = {"interactive": 2, "standard": 1, "batch": 0}


def _tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {token for token in re.split(r"[^a-zA-Z0-9]+", value.lower()) if token}


def _router_payload_builder(args: BaseModel, _ctx: ToolContext) -> Any:
    typed = RouterDelegationArgs.model_validate(args)
    return typed.payload or {"query": typed.query}


def _card_dict(card: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(card, BaseModel):
        return card.model_dump(by_alias=True, exclude_none=True)
    return dict(card)


def _capability_bool(card: Mapping[str, Any], name: str) -> bool:
    capabilities = card.get("capabilities") or {}
    if isinstance(capabilities, Mapping):
        return bool(capabilities.get(name))
    if isinstance(capabilities, Sequence) and not isinstance(capabilities, str):
        return name in capabilities
    return False


def _skill_id(skill: Mapping[str, Any]) -> str:
    value = skill.get("id") or skill.get("name")
    return str(value or "default")


def _normalize_agent_url(value: str) -> str:
    return value.rstrip("/")


@dataclass(slots=True)
class RemoteSkillRecord:
    """A routable skill exposed by a remote A2A agent."""

    agent_url: str
    skill_id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    input_modes: tuple[str, ...] = ()
    output_modes: tuple[str, ...] = ()
    security: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RemoteAgentRecord:
    """A registered A2A agent with normalized routing metadata."""

    agent_url: str
    name: str
    description: str
    version: str
    skills: tuple[RemoteSkillRecord, ...]
    streaming: bool = False
    push_notifications: bool = False
    tenant_id: str | None = None
    trust_tier: TrustTier = "standard"
    latency_tier: LatencyTier = "standard"
    auth_schemes: tuple[str, ...] = ()
    raw_card: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRouteRequest:
    """Inputs used to score agents for a router delegation."""

    query: str
    skill: str | None = None
    input_mode: str | None = None
    output_mode: str | None = None
    tenant_id: str | None = None
    required_execution_mode: ExecutionMode | None = None
    auth_schemes: tuple[str, ...] = ()
    min_trust_tier: TrustTier | None = None


@dataclass(slots=True)
class RouterPolicy:
    """Production routing guardrails applied before route scoring."""

    allowed_agents: tuple[str, ...] = ()
    denied_agents: tuple[str, ...] = ()
    max_candidates: int = 5
    require_same_tenant: bool = True
    min_trust_tier: TrustTier | None = None
    required_execution_mode: ExecutionMode | None = None
    required_auth_schemes: tuple[str, ...] = ()
    fallback_agent_url: str | None = None
    fallback_skill: str | None = None
    timeout_s: float | None = None
    poll_interval_s: float = 0.25
    max_poll_attempts: int = 120


@dataclass(slots=True)
class AgentRouteCandidate:
    """A scored route candidate."""

    agent: RemoteAgentRecord
    skill: RemoteSkillRecord
    score: float
    reasons: tuple[str, ...]


class AgentRegistry:
    """In-memory A2A agent registry with explainable route scoring."""

    def __init__(self) -> None:
        self._agents: dict[str, RemoteAgentRecord] = {}

    def register(self, record: RemoteAgentRecord) -> None:
        self._agents[_normalize_agent_url(record.agent_url)] = record

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> AgentRegistry:
        """Build a registry from a declarative mapping.

        Supported shape:
        {"agents": [{"agent_url": "...", "card": {...}, "tenant_id": "..."}]}
        """

        registry = cls()
        agents = config.get("agents") or ()
        if not isinstance(agents, Sequence) or isinstance(agents, str):
            raise ValueError("A2A registry config must contain an 'agents' list")
        for index, raw_agent in enumerate(agents):
            if not isinstance(raw_agent, Mapping):
                raise ValueError(f"A2A registry config agents[{index}] must be an object")
            card = raw_agent.get("card")
            if not isinstance(card, Mapping):
                raise ValueError(f"A2A registry config agents[{index}].card must be an object")
            agent_url = raw_agent.get("agent_url") or raw_agent.get("agentUrl") or card.get("url")
            if not isinstance(agent_url, str) or not agent_url.strip():
                raise ValueError(f"A2A registry config agents[{index}] must define agent_url")
            registry.register_card(
                agent_url=agent_url.strip(),
                card=card,
                tenant_id=_optional_str(raw_agent.get("tenant_id") or raw_agent.get("tenantId")),
                trust_tier=_trust_tier(raw_agent.get("trust_tier") or raw_agent.get("trustTier")),
                latency_tier=_latency_tier(raw_agent.get("latency_tier") or raw_agent.get("latencyTier")),
                auth_schemes=_string_tuple(raw_agent.get("auth_schemes") or raw_agent.get("authSchemes")),
                metadata=_mapping_or_empty(raw_agent.get("metadata")),
            )
        return registry

    def register_card(
        self,
        *,
        agent_url: str,
        card: Mapping[str, Any] | BaseModel,
        tenant_id: str | None = None,
        trust_tier: TrustTier = "standard",
        latency_tier: LatencyTier = "standard",
        auth_schemes: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> RemoteAgentRecord:
        agent_url = _normalize_agent_url(agent_url)
        payload = _card_dict(card)
        skills: list[RemoteSkillRecord] = []
        for raw_skill in payload.get("skills") or []:
            if not isinstance(raw_skill, Mapping):
                continue
            skill_id = _skill_id(raw_skill)
            input_modes = raw_skill.get("inputModes") or raw_skill.get("input_modes") or ()
            output_modes = raw_skill.get("outputModes") or raw_skill.get("output_modes") or ()
            skills.append(
                RemoteSkillRecord(
                    agent_url=agent_url,
                    skill_id=skill_id,
                    name=str(raw_skill.get("name") or skill_id),
                    description=str(raw_skill.get("description") or ""),
                    tags=tuple(str(tag) for tag in (raw_skill.get("tags") or ())),
                    input_modes=tuple(str(mode) for mode in input_modes),
                    output_modes=tuple(str(mode) for mode in output_modes),
                    security=tuple(raw_skill.get("security") or ()),
                    metadata=dict(raw_skill),
                )
            )
        if not skills:
            skills.append(
                RemoteSkillRecord(
                    agent_url=agent_url,
                    skill_id="default",
                    name=str(payload.get("name") or "default"),
                    description=str(payload.get("description") or ""),
                )
            )
        record = RemoteAgentRecord(
            agent_url=agent_url,
            name=str(payload.get("name") or agent_url),
            description=str(payload.get("description") or ""),
            version=str(payload.get("version") or "unknown"),
            skills=tuple(skills),
            streaming=_capability_bool(payload, "streaming"),
            push_notifications=_capability_bool(payload, "pushNotifications")
            or _capability_bool(payload, "push_notifications"),
            tenant_id=tenant_id,
            trust_tier=trust_tier,
            latency_tier=latency_tier,
            auth_schemes=tuple(str(scheme) for scheme in auth_schemes),
            raw_card=payload,
            metadata=dict(metadata or {}),
        )
        self.register(record)
        return record

    def agents(self) -> tuple[RemoteAgentRecord, ...]:
        return tuple(self._agents.values())

    def route(
        self,
        request: AgentRouteRequest,
        *,
        limit: int | None = None,
        policy: RouterPolicy | None = None,
    ) -> list[AgentRouteCandidate]:
        effective_limit = limit if limit is not None else (policy.max_candidates if policy is not None else 5)
        if effective_limit <= 0:
            return []
        candidates: list[AgentRouteCandidate] = []
        for agent in self._agents.values():
            if not self._meets_route_constraints(agent, request, policy):
                continue
            for skill in agent.skills:
                scored = self._score(agent, skill, request)
                if scored is not None:
                    candidates.append(scored)
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        if not candidates and policy is not None:
            fallback = self._fallback_candidate(policy)
            if fallback is not None and self._meets_route_constraints(fallback.agent, request, policy):
                candidates.append(fallback)
        return candidates[:effective_limit]

    def best(self, request: AgentRouteRequest, *, policy: RouterPolicy | None = None) -> AgentRouteCandidate | None:
        candidates = self.route(request, limit=1, policy=policy)
        return candidates[0] if candidates else None

    def _allowed_by_policy(
        self,
        agent: RemoteAgentRecord,
        request: AgentRouteRequest,
        policy: RouterPolicy | None,
    ) -> bool:
        if request.tenant_id is not None and agent.tenant_id is not None and agent.tenant_id != request.tenant_id:
            return False
        if policy is None:
            return True
        agent_url = _normalize_agent_url(agent.agent_url)
        allowed = {_normalize_agent_url(value) for value in policy.allowed_agents}
        denied = {_normalize_agent_url(value) for value in policy.denied_agents}
        if allowed and agent_url not in allowed:
            return False
        if denied and agent_url in denied:
            return False
        if policy.require_same_tenant and request.tenant_id is not None and agent.tenant_id != request.tenant_id:
            return False
        return True

    def _meets_route_constraints(
        self,
        agent: RemoteAgentRecord,
        request: AgentRouteRequest,
        policy: RouterPolicy | None,
    ) -> bool:
        if not self._allowed_by_policy(agent, request, policy):
            return False
        min_trust_tier = request.min_trust_tier or (policy.min_trust_tier if policy is not None else None)
        if min_trust_tier is not None and _TRUST_SCORE[agent.trust_tier] < _TRUST_SCORE[min_trust_tier]:
            return False
        required_execution_mode = request.required_execution_mode or (
            policy.required_execution_mode if policy is not None else None
        )
        if required_execution_mode == "stream" and not agent.streaming:
            return False
        auth_schemes = request.auth_schemes or (policy.required_auth_schemes if policy is not None else ())
        if auth_schemes and (not agent.auth_schemes or not set(auth_schemes).intersection(agent.auth_schemes)):
            return False
        return True

    def _fallback_candidate(self, policy: RouterPolicy) -> AgentRouteCandidate | None:
        if policy.fallback_agent_url is None:
            return None
        agent = self._agents.get(_normalize_agent_url(policy.fallback_agent_url))
        if agent is None:
            return None
        skill = next(
            (item for item in agent.skills if item.skill_id == policy.fallback_skill),
            agent.skills[0] if agent.skills else None,
        )
        if skill is None:
            return None
        return AgentRouteCandidate(agent=agent, skill=skill, score=0.0, reasons=("policy_fallback",))

    def _score(
        self,
        agent: RemoteAgentRecord,
        skill: RemoteSkillRecord,
        request: AgentRouteRequest,
    ) -> AgentRouteCandidate | None:
        reasons: list[str] = []
        score = 0.0
        query_tokens = _tokens(request.query)
        requested_skill_tokens = _tokens(request.skill)
        skill_tokens = _tokens(skill.skill_id) | _tokens(skill.name) | _tokens(skill.description) | set(skill.tags)
        agent_tokens = _tokens(agent.name) | _tokens(agent.description)

        if requested_skill_tokens:
            overlap = requested_skill_tokens & skill_tokens
            if not overlap:
                return None
            score += 40 + (10 * len(overlap))
            reasons.append(f"skill_match:{','.join(sorted(overlap))}")

        overlap = query_tokens & (skill_tokens | agent_tokens)
        if overlap:
            score += min(30, 5 * len(overlap))
            reasons.append(f"query_overlap:{','.join(sorted(overlap))}")

        if request.input_mode and skill.input_modes and request.input_mode not in skill.input_modes:
            return None
        if request.input_mode and request.input_mode in skill.input_modes:
            score += 5
            reasons.append(f"input_mode:{request.input_mode}")

        if request.output_mode and skill.output_modes and request.output_mode not in skill.output_modes:
            return None
        if request.output_mode and request.output_mode in skill.output_modes:
            score += 5
            reasons.append(f"output_mode:{request.output_mode}")

        if request.required_execution_mode == "stream" and agent.streaming:
            score += 8
            reasons.append("streaming")
        elif request.required_execution_mode == "task":
            score += 3
            reasons.append("task_capable")

        score += _TRUST_SCORE[agent.trust_tier] * 4
        score += _LATENCY_SCORE[agent.latency_tier] * 2
        reasons.append(f"trust:{agent.trust_tier}")
        reasons.append(f"latency:{agent.latency_tier}")

        if not reasons:
            reasons.append("fallback")
        return AgentRouteCandidate(agent=agent, skill=skill, score=score, reasons=tuple(reasons))


class RouterDelegationArgs(BaseModel):
    query: str
    skill: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    execution_mode: ExecutionMode | None = None
    input_mode: str | None = None
    output_mode: str | None = None


class RouterDelegationResult(BaseModel):
    agent_url: str
    skill: str
    score: float
    reasons: list[str]
    result: Any
    route_metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class A2ARouterToolset:
    """Planner-callable router backed by an :class:`AgentRegistry`."""

    registry: AgentRegistry
    transport: RemoteTransport
    default_execution_mode: ExecutionMode = "auto"
    default_timeout_s: float | None = None
    policy: RouterPolicy = field(default_factory=RouterPolicy)

    def tool(
        self,
        *,
        name: str = "delegate_to_agent",
        desc: str = "Delegate a request to the best matching A2A specialist.",
        side_effects: SideEffect = "external",
        tags: Sequence[str] = ("a2a", "router", "remote"),
    ) -> NodeSpec:
        async def _impl(args: RouterDelegationArgs, ctx: ToolContext) -> RouterDelegationResult:
            tenant = ctx.tool_context.get("tenant_id") or ctx.tool_context.get("tenant")
            execution_mode = args.execution_mode or self.policy.required_execution_mode or self.default_execution_mode
            request = AgentRouteRequest(
                query=args.query,
                skill=args.skill,
                input_mode=args.input_mode,
                output_mode=args.output_mode,
                tenant_id=str(tenant).strip() if tenant is not None and str(tenant).strip() else None,
                required_execution_mode=execution_mode,
                auth_schemes=self.policy.required_auth_schemes,
                min_trust_tier=self.policy.min_trust_tier,
            )
            candidates = self.registry.route(request, policy=self.policy)
            if not candidates:
                raise RuntimeError("No compatible A2A specialist found")
            candidate = candidates[0]

            toolset = A2AAgentToolset(
                agent_url=candidate.agent.agent_url,
                transport=self.transport,
                agent_card=candidate.agent.raw_card,
                default_timeout_s=self.policy.timeout_s or self.default_timeout_s,
            )
            spec = toolset.tool(
                name=f"{name}_{candidate.skill.skill_id}",
                skill=candidate.skill.skill_id,
                args_model=RouterDelegationArgs,
                out_model=RouterDelegationResult,
                desc=f"Delegate to {candidate.agent.name}/{candidate.skill.name}",
                execution_mode=execution_mode,
                poll_interval_s=self.policy.poll_interval_s,
                max_poll_attempts=self.policy.max_poll_attempts,
                payload_builder=_router_payload_builder,
            )
            result = await spec.node.func(args, ctx)
            return RouterDelegationResult(
                agent_url=candidate.agent.agent_url,
                skill=candidate.skill.skill_id,
                score=candidate.score,
                reasons=list(candidate.reasons),
                result=result,
                route_metadata={
                    "candidate_count": len(candidates),
                    "execution_mode": execution_mode,
                    "agent_name": candidate.agent.name,
                    "trust_tier": candidate.agent.trust_tier,
                    "latency_tier": candidate.agent.latency_tier,
                    "tenant_id": candidate.agent.tenant_id,
                },
            )

        return NodeSpec(
            node=Node(_impl, name=name, policy=NodePolicy(validate="none")),
            name=name,
            desc=desc,
            args_model=RouterDelegationArgs,
            out_model=RouterDelegationResult,
            side_effects=side_effects,
            tags=tuple(tags),
            extra={
                "a2a_router": {
                    "agents": len(self.registry.agents()),
                    "max_candidates": self.policy.max_candidates,
                    "required_execution_mode": self.policy.required_execution_mode,
                    "min_trust_tier": self.policy.min_trust_tier,
                }
            },
        )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    raise ValueError("Expected string or sequence of strings")


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    raise ValueError("Expected object metadata")


def _trust_tier(value: Any) -> TrustTier:
    text = str(value or "standard")
    if text not in _TRUST_SCORE:
        raise ValueError(f"Invalid A2A trust tier: {text}")
    return text  # type: ignore[return-value]


def _latency_tier(value: Any) -> LatencyTier:
    text = str(value or "standard")
    if text not in _LATENCY_SCORE:
        raise ValueError(f"Invalid A2A latency tier: {text}")
    return text  # type: ignore[return-value]


def load_agent_registry_config(path: str | Path) -> AgentRegistry:
    """Load an :class:`AgentRegistry` from JSON, YAML, or TOML config."""

    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    suffix = config_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        yaml = import_module("yaml")
        payload = yaml.safe_load(text)
    elif suffix == ".toml":
        payload = tomllib.loads(text)
    else:
        raise ValueError("A2A registry config must be .json, .yaml, .yml, or .toml")
    if not isinstance(payload, Mapping):
        raise ValueError("A2A registry config root must be an object")
    return AgentRegistry.from_config(payload)


async def fetch_agent_card(
    agent_url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError("httpx is required to fetch A2A agent cards. Install penguiflow[a2a-client].") from exc
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        response = await client.get(f"{agent_url.rstrip('/')}/.well-known/agent-card.json", headers=dict(headers or {}))
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Agent card response must be a JSON object")
    return payload


__all__ = [
    "A2ARouterToolset",
    "AgentRegistry",
    "AgentRouteCandidate",
    "AgentRouteRequest",
    "RemoteAgentRecord",
    "RemoteSkillRecord",
    "RouterPolicy",
    "RouterDelegationArgs",
    "RouterDelegationResult",
    "fetch_agent_card",
    "load_agent_registry_config",
]
