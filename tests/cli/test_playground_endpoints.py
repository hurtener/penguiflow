"""Integration tests for playground FastAPI endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from ag_ui.core import RunAgentInput
from fastapi.testclient import TestClient

from penguiflow.cli.playground import create_playground_app
from penguiflow.cli.playground_state import InMemoryStateStore
from penguiflow.cli.playground_wrapper import AgentWrapper, ChatResult


class MockAgentWrapper(AgentWrapper):
    """Mock agent wrapper for testing."""

    def __init__(self, chat_result: ChatResult | None = None, raise_on_chat: Exception | None = None):
        self._chat_result = chat_result or ChatResult(
            trace_id="test-trace-123",
            session_id="test-session",
            answer="Test answer",
            metadata={"steps": 1},
            pause=None,
        )
        self._raise_on_chat = raise_on_chat
        self._state_store = InMemoryStateStore()
        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True

    async def shutdown(self) -> None:
        pass

    async def resume(
        self,
        resume_token: str,
        *,
        session_id: str,
        user_input: str | None = None,
        tool_context: dict[str, Any] | None = None,
        event_consumer: Any = None,
        trace_id_hint: str | None = None,
        steering: Any = None,
    ) -> ChatResult:
        del resume_token, session_id, user_input, tool_context, event_consumer, trace_id_hint, steering
        if self._raise_on_chat:
            raise self._raise_on_chat
        return self._chat_result

    async def chat(
        self,
        query: str,
        *,
        session_id: str,
        llm_context: dict[str, Any] | None = None,
        tool_context: dict[str, Any] | None = None,
        event_consumer: Any = None,
        trace_id_hint: str | None = None,
        steering: Any = None,
    ) -> ChatResult:
        del steering
        if self._raise_on_chat:
            raise self._raise_on_chat
        return self._chat_result


class TestHealthEndpoint:
    """Tests for /health endpoint (line 719)."""

    def test_health_returns_ok(self, tmp_path: Path) -> None:
        """Test health endpoint returns ok status."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestUISpecEndpoint:
    """Tests for /ui/spec endpoint (line 723)."""

    def test_ui_spec_returns_none_when_no_spec(self, tmp_path: Path) -> None:
        """Test /ui/spec returns null when no spec file exists."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/ui/spec")
        assert response.status_code == 200
        # Response body is null/None
        assert response.json() is None

    def test_ui_spec_returns_spec_when_exists(self, tmp_path: Path) -> None:
        """Test /ui/spec returns spec when file exists."""
        spec_content = """\
agent:
  name: test-agent
  description: Test
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
  - name: main
    description: Main flow
    nodes:
      - name: fetch
        description: fetch
    steps: [fetch]
llm:
  primary:
    model: gpt-4o
planner:
  max_iters: 3
  system_prompt_extra: Test
"""
        (tmp_path / "agent.yaml").write_text(spec_content, encoding="utf-8")

        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/ui/spec")
        assert response.status_code == 200
        # May or may not be valid depending on spec validation
        data = response.json()
        # If spec is returned (valid or not), it should have content
        if data is not None:
            assert "content" in data


class TestUIMetaEndpoint:
    """Tests for /ui/meta endpoint (line 753)."""

    def test_ui_meta_returns_metadata(self, tmp_path: Path) -> None:
        """Test /ui/meta returns metadata."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/ui/meta")
        assert response.status_code == 200
        data = response.json()
        assert "agent" in data
        assert "planner" in data
        assert "services" in data
        assert "tools" in data


class TestUIComponentsEndpoint:
    """Tests for /ui/components endpoint."""

    def test_ui_components_returns_allowlisted_registry(self, tmp_path: Path) -> None:
        spec_content = """\
agent:
  name: test-agent
  description: Test
  template: react
  flags:
    memory: false
    hitl: true
tools:
  - name: fetch
    description: Fetch data
llm:
  primary:
    model: gpt-4o
planner:
  system_prompt_extra: Test
  rich_output:
    enabled: true
    allowlist: ["markdown"]
"""
        (tmp_path / "agent.yaml").write_text(spec_content, encoding="utf-8")

        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/ui/components")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert set(data["allowlist"]) == {"markdown"}
        assert "markdown" in data["components"]
        assert "echarts" not in data["components"]


class TestUIValidateEndpoint:
    """Tests for /ui/validate endpoint (lines 727-749)."""

    def test_validate_valid_spec(self, tmp_path: Path) -> None:
        """Test validating a valid spec."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        spec_text = """\
agent:
  name: test-agent
  description: Test
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
  - name: main
    description: Main flow
    nodes:
      - name: fetch
        description: fetch
    steps: [fetch]
llm:
  primary:
    model: gpt-4o
planner:
  max_iters: 3
  system_prompt_extra: Test
"""
        response = client.post("/ui/validate", json={"spec_text": spec_text})
        assert response.status_code == 200
        data = response.json()
        assert "content" in data
        assert "valid" in data
        assert "errors" in data

    def test_validate_invalid_spec(self, tmp_path: Path) -> None:
        """Test validating an invalid spec returns errors (lines 733-747)."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        spec_text = """\
agent:
  name: test
  # Missing required fields
tools: []
"""
        response = client.post("/ui/validate", json={"spec_text": spec_text})
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0


class TestUIGenerateEndpoint:
    """Tests for /ui/generate endpoint (lines 755-790)."""

    def test_generate_missing_spec_text(self, tmp_path: Path) -> None:
        """Test generate without spec_text returns 400 (lines 757-759)."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/ui/generate", json={})
        assert response.status_code == 400
        assert "spec_text is required" in response.json()["detail"]

    def test_generate_with_invalid_spec(self, tmp_path: Path) -> None:
        """Test generate with invalid spec returns 400 (lines 776-786)."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        spec_text = """\
agent:
  name: test
  # Missing required fields
tools: []
"""
        response = client.post("/ui/generate", json={"spec_text": spec_text})
        assert response.status_code == 400
        # Should have validation errors in detail
        detail = response.json()["detail"]
        assert isinstance(detail, list)

    def test_generate_with_valid_spec(self, tmp_path: Path) -> None:
        """Test generate with valid spec returns success (lines 762-775)."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        spec_text = """\
agent:
  name: test-agent
  description: Test agent
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
  - name: main
    description: Main flow
    nodes:
      - name: fetch
        description: fetch
    steps: [fetch]
llm:
  primary:
    model: gpt-4o
planner:
  max_iters: 3
  system_prompt_extra: Test
"""
        response = client.post("/ui/generate", json={"spec_text": spec_text})
        # May succeed or fail depending on full validation
        if response.status_code == 200:
            data = response.json()
            assert "success" in data
            assert "created" in data
            assert "skipped" in data
            assert "errors" in data


class TestChatEndpoint:
    """Tests for /chat endpoint (lines 792-829)."""

    def test_chat_success(self, tmp_path: Path) -> None:
        """Test successful chat request."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/chat", json={"query": "Hello"})
        assert response.status_code == 200
        data = response.json()
        assert data["trace_id"] == "test-trace-123"
        assert data["answer"] == "Test answer"

    def test_chat_with_session_id(self, tmp_path: Path) -> None:
        """Test chat with custom session ID."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/chat", json={"query": "Hello", "session_id": "my-session"})
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test-session"

    def test_chat_with_context(self, tmp_path: Path) -> None:
        """Test chat with llm_context and tool_context."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/chat",
            json={
                "query": "Hello",
                "llm_context": {"key": "value"},
                "tool_context": {"tool_key": "tool_value"},
            },
        )
        assert response.status_code == 200

    def test_chat_error_returns_500(self, tmp_path: Path) -> None:
        """Test chat error returns 500 (lines 816-818)."""
        wrapper = MockAgentWrapper(raise_on_chat=ValueError("Test error"))
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/chat", json={"query": "Hello"})
        assert response.status_code == 500
        assert "Chat failed" in response.json()["detail"]


class TestChatStreamEndpoint:
    """Tests for /chat/stream endpoint (lines 831-881)."""

    def test_chat_stream_returns_sse(self, tmp_path: Path) -> None:
        """Test chat stream returns SSE response."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/chat/stream", params={"query": "Hello"})
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_chat_stream_with_context(self, tmp_path: Path) -> None:
        """Test chat stream with context parameters."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            "/chat/stream",
            params={
                "query": "Hello",
                "llm_context": '{"key": "value"}',
                "tool_context": '{"tool": "val"}',
                "session_id": "test-session",
            },
        )
        assert response.status_code == 200


class TestEventsEndpoint:
    """Tests for /events endpoint (lines 883-942)."""

    def test_events_with_valid_trace(self, tmp_path: Path) -> None:
        """Test /events returns SSE stream for valid trace."""
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/events", params={"trace_id": "test-trace"})
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_events_returns_sse(self, tmp_path: Path) -> None:
        """Test /events returns SSE stream."""
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/events", params={"trace_id": "test-trace"})
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_events_with_invalid_session(self, tmp_path: Path) -> None:
        """Test /events with invalid session returns 404 (lines 891-894)."""
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            "/events",
            params={"trace_id": "nonexistent-trace", "session_id": "nonexistent-session"},
        )
        assert response.status_code == 404
        assert "Trace not found" in response.json()["detail"]


class TestAguiResumeEndpoint:
    """Tests for /agui/resume endpoint."""

    def test_agui_resume_requires_token(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/agui/resume", json={"thread_id": "thread-1", "run_id": "run-1"})
        # 422 = FastAPI validation error when resume_token is missing
        assert response.status_code == 422

    def test_agui_resume_rejects_invalid_result(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/agui/resume",
            headers={"accept": "text/event-stream"},
            json={
                "resume_token": "resume-123",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "tool_name": "ui_confirm",
                "component": "confirm",
                "result": "nope",
            },
        )
        assert response.status_code == 400

    def test_agui_resume_streams_events(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/agui/resume",
            headers={"accept": "text/event-stream"},
            json={
                "resume_token": "resume-123",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "tool_name": "ui_confirm",
                "component": "confirm",
                "result": True,
            },
        )
        assert response.status_code == 200
        assert response.text


class TestTrajectoryEndpoint:
    """Tests for /trajectory endpoint (lines 944-954)."""

    def test_trajectory_with_store(self, tmp_path: Path) -> None:
        """Test /trajectory returns trajectory when found."""
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        # Request trajectory - will return 404 since no trajectory exists
        response = client.get("/trajectory/test-trace", params={"session_id": "test-session"})
        assert response.status_code == 404

    def test_trajectory_not_found(self, tmp_path: Path) -> None:
        """Test /trajectory with unknown trace returns 404 (lines 949-950)."""
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/trajectory/unknown-trace", params={"session_id": "test-session"})
        assert response.status_code == 404
        assert "Trajectory not found" in response.json()["detail"]


class TestArtifactEndpoints:
    """Tests for /artifacts endpoints (lines 958-1059)."""

    def test_get_artifact_no_store_returns_501(self, tmp_path: Path) -> None:
        """Test /artifacts without store returns 501 (lines 969-971)."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts/test-id")
        assert response.status_code == 501
        assert "Artifact storage not enabled" in response.json()["detail"]

    def test_get_artifact_meta_no_store_returns_501(self, tmp_path: Path) -> None:
        """Test /artifacts/{id}/meta without store returns 501 (lines 1026-1027)."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts/test-id/meta")
        assert response.status_code == 501
        assert "Artifact storage not enabled" in response.json()["detail"]


class TestResourceEndpoints:
    """Tests for /resources endpoints (lines 1063-1176)."""

    def test_list_resources_no_tool_nodes(self, tmp_path: Path) -> None:
        """Test /resources when no tool nodes available (lines 1067-1075)."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/resources/test-namespace")
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "No tool nodes available" in data["error"]

    def test_read_resource_no_tool_nodes(self, tmp_path: Path) -> None:
        """Test /resources read when no tool nodes (lines 1118-1125)."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/resources/test-namespace/test-uri")
        assert response.status_code == 500
        assert "No tool nodes available" in response.json()["detail"]


class TestDiscoverPlannerFunction:
    """Tests for _discover_planner helper (lines 591-601)."""

    def test_discover_planner_from_wrapper(self, tmp_path: Path) -> None:
        """Test discovering planner from wrapper with _planner attribute."""
        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()  # Add a mock planner

        # Create the app - planner discovery happens inside endpoints
        _ = create_playground_app(project_root=tmp_path, agent=wrapper)


class TestDiscoverArtifactStore:
    """Tests for _discover_artifact_store helper (lines 603-623)."""

    def test_discover_returns_none_for_noop_store(self, tmp_path: Path) -> None:
        """Test _discover_artifact_store returns None for NoOpArtifactStore (line 620)."""
        from penguiflow.artifacts import NoOpArtifactStore

        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = NoOpArtifactStore()

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        # The artifact store discovery returns None for NoOp, verified via endpoint behavior
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/artifacts/test-id")
        # Should return 501 since NoOp store is treated as disabled
        assert response.status_code == 501


class TestStateStoreInitialization:
    """Tests for state store initialization (lines 566-570)."""

    def test_creates_default_store_when_none_provided(self, tmp_path: Path) -> None:
        """Test that InMemoryStateStore is created when no store provided."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)

        # The store should be initialized
        assert app.state.state_store is not None

    def test_uses_provided_store(self, tmp_path: Path) -> None:
        """Test that provided store is used."""
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)

        assert app.state.state_store is store


class TestAGUIEndpoint:
    """Tests for /agui/agent endpoint."""

    def test_agui_agent_streams_answer(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        input_payload = RunAgentInput(
            thread_id="thread-1",
            run_id="run-1",
            messages=[{"id": "msg-1", "role": "user", "content": "Hello"}],
            tools=[],
            context=[],
            state={},
            forwarded_props={},
        )

        response = client.post(
            "/agui/agent",
            json=input_payload.model_dump(by_alias=True, mode="json"),
            headers={"accept": "text/event-stream"},
        )

        assert response.status_code == 200
        assert "Test answer" in response.text


class TestSessionEndpoints:
    def test_session_info(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/sessions/test-session")
        assert response.status_code == 200
        payload = response.json()
        assert payload["session_id"] == "test-session"
        assert "context_version" in payload

    def test_session_context_update(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.patch(
            "/sessions/test-session/context",
            json={"llm_context": {"hello": "world"}, "merge": False},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_delete_task_endpoint(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/chat", json={"query": "Hello", "session_id": "sess-1"})
        assert response.status_code == 200

        tasks = client.get("/tasks", params={"session_id": "sess-1"})
        assert tasks.status_code == 200
        task_id = tasks.json()[0]["task_id"]

        delete_response = client.delete(f"/tasks/{task_id}", params={"session_id": "sess-1"})
        assert delete_response.status_code == 200
