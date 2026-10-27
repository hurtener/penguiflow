"""Coverage for Enterprise v2 OpenTelemetry runtime tracing."""

from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import examples.planner_enterprise_agent_v2.main as enterprise_main
from examples.planner_enterprise_agent_v2.config import AgentConfig


@pytest.mark.asyncio
async def test_execute_uses_otel_without_mlflow_runtime_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    spans: list[tuple[str, dict[str, object]]] = []

    class FakeTracer:
        @contextmanager
        def start_as_current_span(self, name: str, *, attributes: dict[str, object]):
            spans.append((name, attributes))
            yield SimpleNamespace(get_span_context=lambda: SimpleNamespace(trace_id=0x0123456789ABCDEF))

    class FakeTrace:
        @staticmethod
        def get_tracer(name: str) -> FakeTracer:
            calls.append(("tracer", name))
            return FakeTracer()

    class FakePlanner:
        async def run(self, **_kwargs):
            return SimpleNamespace(
                reason="answer_complete",
                payload={"text": "ok"},
                metadata={},
            )

    monkeypatch.setattr(enterprise_main, "trace", FakeTrace)
    monkeypatch.delenv("PENGUIFLOW_LLM_TRACING", raising=False)
    monkeypatch.setattr(enterprise_main.EnterpriseAgentOrchestrator, "_build_nodes", lambda _self: [])
    monkeypatch.setattr(enterprise_main.EnterpriseAgentOrchestrator, "_build_registry", lambda _self: object())
    monkeypatch.setattr(
        enterprise_main.EnterpriseAgentOrchestrator,
        "_build_planner",
        lambda _self: FakePlanner(),
    )

    agent = enterprise_main.EnterpriseAgentOrchestrator(AgentConfig.from_env())
    result = await agent.execute("status")

    assert result.text == "ok"
    assert result.metadata["otel_trace_id"] == "00000000000000000123456789abcdef"
    assert not hasattr(enterprise_main, "mlflow")
    assert os.getenv("PENGUIFLOW_LLM_TRACING") == "otel"
    assert calls == [("tracer", "penguiflow.enterprise_agent_v2")]
    assert spans == [
        (
            "enterprise_agent_v2",
            {
                "gen_ai.operation.name": "invoke_agent",
                "penguiflow.agent.name": "enterprise_agent_v2",
            },
        )
    ]
