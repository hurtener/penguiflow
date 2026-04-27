from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from penguiflow.remote import RemoteCallResult
from penguiflow_a2a.registry import (
    A2ARouterToolset,
    AgentRegistry,
    AgentRouteRequest,
    RouterDelegationArgs,
    RouterPolicy,
    load_agent_registry_config,
)


def _card(name: str, skill_id: str, description: str, *, streaming: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "version": "1.0.0",
        "capabilities": {"streaming": streaming, "pushNotifications": False},
        "skills": [
            {
                "id": skill_id,
                "name": skill_id.title(),
                "description": description,
                "tags": [skill_id],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
        ],
    }


def test_agent_registry_scores_by_skill_metadata_and_tenant() -> None:
    registry = AgentRegistry()
    registry.register_card(
        agent_url="http://weather",
        card=_card("Weather Agent", "forecast", "weather forecast and climate lookup"),
        tenant_id="tenant-a",
        trust_tier="trusted",
        latency_tier="interactive",
    )
    registry.register_card(
        agent_url="http://math",
        card=_card("Math Agent", "calculate", "numeric calculation and algebra"),
        tenant_id="tenant-a",
        trust_tier="standard",
        latency_tier="standard",
    )

    best = registry.best(AgentRouteRequest(query="need a weather forecast", tenant_id="tenant-a"))

    assert best is not None
    assert best.agent.agent_url == "http://weather"
    assert best.skill.skill_id == "forecast"
    assert any(reason.startswith("query_overlap:") for reason in best.reasons)
    assert registry.best(AgentRouteRequest(query="weather", tenant_id="tenant-b")) is None


def test_agent_registry_filters_required_streaming_mode() -> None:
    registry = AgentRegistry()
    registry.register_card(agent_url="http://batch", card=_card("Batch", "report", "report generation"))
    registry.register_card(
        agent_url="http://stream",
        card=_card("Stream", "report", "report generation", streaming=True),
    )

    best = registry.best(AgentRouteRequest(query="report", required_execution_mode="stream"))

    assert best is not None
    assert best.agent.agent_url == "http://stream"


def test_agent_registry_policy_filters_agents_and_auth() -> None:
    registry = AgentRegistry()
    registry.register_card(
        agent_url="http://low",
        card=_card("Low Trust", "forecast", "weather forecast"),
        tenant_id="tenant-a",
        trust_tier="low",
        auth_schemes=["bearer"],
    )
    registry.register_card(
        agent_url="http://trusted",
        card=_card("Trusted", "forecast", "weather forecast"),
        tenant_id="tenant-a",
        trust_tier="trusted",
        auth_schemes=["bearer"],
    )
    registry.register_card(
        agent_url="http://denied",
        card=_card("Denied", "forecast", "weather forecast"),
        tenant_id="tenant-a",
        trust_tier="critical",
        auth_schemes=["bearer"],
    )

    candidates = registry.route(
        AgentRouteRequest(query="weather", tenant_id="tenant-a"),
        policy=RouterPolicy(
            denied_agents=("http://denied",),
            min_trust_tier="trusted",
            required_auth_schemes=("bearer",),
        ),
    )

    assert [candidate.agent.agent_url for candidate in candidates] == ["http://trusted"]


def test_agent_registry_policy_fallback_does_not_bypass_guardrails() -> None:
    registry = AgentRegistry()
    registry.register_card(
        agent_url="http://fallback",
        card=_card("Fallback", "forecast", "weather forecast"),
        tenant_id="tenant-a",
        trust_tier="low",
    )

    candidates = registry.route(
        AgentRouteRequest(query="unmatched", skill="unknown", tenant_id="tenant-a"),
        policy=RouterPolicy(fallback_agent_url="http://fallback", min_trust_tier="trusted"),
    )

    assert candidates == []


def test_agent_registry_loads_declarative_yaml_config(tmp_path) -> None:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        """
agents:
  - agent_url: http://weather
    tenant_id: tenant-a
    trust_tier: trusted
    latency_tier: interactive
    auth_schemes: [bearer]
    card:
      name: Weather Agent
      description: Weather lookup
      version: 1.0.0
      capabilities:
        streaming: true
      skills:
        - id: forecast
          name: Forecast
          description: Weather forecast
          tags: [weather]
""",
        encoding="utf-8",
    )

    registry = load_agent_registry_config(config_path)
    best = registry.best(AgentRouteRequest(query="weather forecast", tenant_id="tenant-a"))

    assert best is not None
    assert best.agent.agent_url == "http://weather"
    assert best.agent.auth_schemes == ("bearer",)
    assert best.agent.streaming is True


class _FakeCtx:
    def __init__(self) -> None:
        self._tool_context = {"tenant": "tenant-a", "session_id": "router-session"}

    @property
    def llm_context(self) -> Mapping[str, Any]:
        return {}

    @property
    def tool_context(self) -> dict[str, Any]:
        return self._tool_context

    @property
    def meta(self) -> dict[str, Any]:
        return dict(self._tool_context)

    async def pause(self, reason: str, payload: Mapping[str, Any] | None = None) -> Any:  # pragma: no cover
        del reason, payload
        raise AssertionError("pause not expected")

    async def emit_chunk(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        del args, kwargs

    async def emit_artifact(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        del args, kwargs


class _FakeTransport:
    def __init__(self) -> None:
        self.requests = []

    async def send(self, request):
        self.requests.append(request)
        return RemoteCallResult(
            result={"handled_by": request.agent_url, "payload": request.message.payload},
            context_id="ctx-remote",
            task_id="task-remote",
            agent_url=request.agent_url,
        )

    async def cancel(self, *, agent_url: str, task_id: str) -> None:  # pragma: no cover
        del agent_url, task_id

    async def stream(self, request):  # pragma: no cover
        del request
        raise AssertionError("stream not expected")


@pytest.mark.asyncio
async def test_a2a_router_toolset_delegates_to_best_candidate() -> None:
    registry = AgentRegistry()
    registry.register_card(
        agent_url="http://weather",
        card=_card("Weather Agent", "forecast", "weather forecast lookup"),
        tenant_id="tenant-a",
    )
    transport = _FakeTransport()
    spec = A2ARouterToolset(registry=registry, transport=transport, default_execution_mode="blocking").tool()

    result = await spec.node.func(
        RouterDelegationArgs(query="weather tomorrow", payload={"city": "Buenos Aires"}),
        _FakeCtx(),
    )

    assert result.agent_url == "http://weather"
    assert result.skill == "forecast"
    assert result.result == {"handled_by": "http://weather", "payload": {"city": "Buenos Aires"}}
    assert result.route_metadata["candidate_count"] == 1
    assert result.route_metadata["execution_mode"] == "blocking"
    assert transport.requests[0].skill == "forecast"


@pytest.mark.asyncio
async def test_a2a_router_toolset_honors_policy() -> None:
    registry = AgentRegistry()
    registry.register_card(
        agent_url="http://standard",
        card=_card("Standard", "forecast", "weather forecast lookup"),
        tenant_id="tenant-a",
        trust_tier="standard",
    )
    registry.register_card(
        agent_url="http://trusted",
        card=_card("Trusted", "forecast", "weather forecast lookup"),
        tenant_id="tenant-a",
        trust_tier="trusted",
    )
    transport = _FakeTransport()
    spec = A2ARouterToolset(
        registry=registry,
        transport=transport,
        default_execution_mode="blocking",
        policy=RouterPolicy(min_trust_tier="trusted", max_candidates=2),
    ).tool()

    result = await spec.node.func(
        RouterDelegationArgs(query="weather tomorrow", payload={"city": "Buenos Aires"}),
        _FakeCtx(),
    )

    assert result.agent_url == "http://trusted"
    assert transport.requests[0].agent_url == "http://trusted"
