"""Backend tests for the playground FastAPI server and discovery."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from penguiflow.cli.generate import run_generate
from penguiflow.cli.playground import ChatRequest, ChatResponse, create_playground_app, discover_agent
from penguiflow.cli.playground_state import InMemoryStateStore
from penguiflow.cli.playground_wrapper import OrchestratorAgentWrapper, PlannerAgentWrapper
from penguiflow.planner import PlannerEvent, PlannerFinish, Trajectory


class _DummyPlanner:
    def __init__(self) -> None:
        self._event_callback = None

    async def run(self, query: str, *, llm_context, tool_context) -> PlannerFinish:  # type: ignore[override]
        del llm_context, tool_context
        if self._event_callback:
            self._event_callback(
                PlannerEvent(
                    event_type="step_start",
                    ts=time.time(),
                    trajectory_step=0,
                    thought="begin",
                )
            )
        return PlannerFinish(
            reason="answer_complete",
            payload={"answer": f"echo:{query}"},
            metadata={"steps": []},
        )


class _DummyResponse:
    def __init__(self, answer: str, trace_id: str, metadata: dict[str, object]) -> None:
        self.answer = answer
        self.trace_id = trace_id
        self.metadata = metadata


class _DummyOrchestrator:
    def __init__(self) -> None:
        self._planner = _DummyPlanner()

    async def execute(self, query: str, *, tenant_id: str, user_id: str, session_id: str) -> _DummyResponse:  # type: ignore[override]
        del tenant_id, user_id, session_id
        result = await self._planner.run(query=query, llm_context={}, tool_context={})
        return _DummyResponse(
            answer=str(result.payload["answer"]),
            trace_id="orch-trace",
            metadata=result.metadata,
        )


def _write_spec(path: Path) -> None:
    path.write_text(
        """\
agent:
  name: demo-gen
  description: Demo agent
  template: react
  flags:
    memory: false
tools:
  - name: fetch
    description: Fetch data
    side_effects: read
    args:
      query: str
    result:
      text: str
flows:
  - name: pipeline
    description: Demo flow
    nodes:
      - name: fetch
        description: Fetch data
    steps: [fetch]
llm:
  primary:
    model: gpt-4o
planner:
  max_iters: 3
  system_prompt_extra: |
    Be helpful.
""",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_state_store_isolates_sessions() -> None:
    store = InMemoryStateStore()
    t1 = Trajectory(query="hello")
    t2 = Trajectory(query="world")
    await store.save_trajectory("trace-1", "session-a", t1)
    await store.save_trajectory("trace-2", "session-a", t2)

    assert await store.get_trajectory("trace-1", "session-a") is t1
    assert await store.get_trajectory("trace-1", "session-b") is None
    assert await store.list_traces("session-a") == ["trace-2", "trace-1"]
    assert await store.list_traces("session-b") == []


@pytest.mark.asyncio
async def test_wrappers_record_events_and_trajectory() -> None:
    store = InMemoryStateStore()
    wrapper = PlannerAgentWrapper(_DummyPlanner(), state_store=store)
    result = await wrapper.chat(query="ping", session_id="sess-1")

    events = await store.get_events(result.trace_id)
    assert events and events[0].event_type == "step_start"
    trajectory = await store.get_trajectory(result.trace_id, "sess-1")
    assert trajectory is not None
    assert trajectory.query == "ping"
    assert result.answer == "echo:ping"


@pytest.mark.asyncio
async def test_orchestrator_wrapper_uses_planner_callback() -> None:
    store = InMemoryStateStore()
    wrapper = OrchestratorAgentWrapper(_DummyOrchestrator(), state_store=store)
    result = await wrapper.chat(query="hi", session_id="demo-session")

    events = await store.get_events("orch-trace")
    assert events and events[0].event_type == "step_start"
    assert result.trace_id == "orch-trace"


def test_chat_endpoint_returns_response() -> None:
    store = InMemoryStateStore()
    agent = PlannerAgentWrapper(_DummyPlanner(), state_store=store)
    app = create_playground_app(agent=agent, state_store=store)
    client = TestClient(app)

    response = client.post("/chat", json={"query": "hello", "session_id": "sess-ui"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "echo:hello"
    assert payload["session_id"] == "sess-ui"
    assert payload["trace_id"]
    # Ensure FastAPI response model serialization succeeds
    ChatResponse(**payload)
    ChatRequest(query="hello")

    trace_events = asyncio.run(store.get_events(payload["trace_id"]))
    assert trace_events


def test_discovery_finds_generated_orchestrator(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    _write_spec(spec_path)
    result = run_generate(
        spec_path=spec_path,
        output_dir=tmp_path,
        force=True,
        quiet=True,
    )
    assert result.success

    project_root = tmp_path / "demo-gen"
    discovery = discover_agent(project_root)
    assert discovery.kind in {"orchestrator", "planner"}
    assert discovery.package == "demo_gen"
    if discovery.kind == "orchestrator":
        assert discovery.target.__name__.endswith("Orchestrator")
    else:
        assert discovery.target.__name__ == "build_planner"


def test_discovery_prefers_orchestrator(tmp_path: Path) -> None:
    src_dir = tmp_path / "src" / "stub_agent"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "config.py").write_text(
        "class Config:\n"
        "    @classmethod\n"
        "    def from_env(cls):\n"
        "        return cls()\n",
        encoding="utf-8",
    )
    (src_dir / "planner.py").write_text(
        "from penguiflow.planner import ReactPlanner\n"
        "def build_planner(config):\n"
        "    return ReactPlanner(nodes=[], registry=None)\n",
        encoding="utf-8",
    )
    (src_dir / "orchestrator.py").write_text(
        "class StubOrchestrator:\n"
        "    def __init__(self, config=None):\n"
        "        self.config = config\n"
        "    async def execute(self, query, *, tenant_id, user_id, session_id):\n"
        "        return type('R', (), {'answer': query, 'trace_id': 't-id', 'metadata': {}})()\n",
        encoding="utf-8",
    )

    discovery = discover_agent(tmp_path)
    assert discovery.kind == "orchestrator"
    assert discovery.target.__name__ == "StubOrchestrator"
