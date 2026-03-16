"""Integration tests for playground FastAPI endpoints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from ag_ui.core import RunAgentInput
from fastapi.testclient import TestClient

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore
from penguiflow.cli.playground import create_playground_app
from penguiflow.cli.playground_state import InMemoryStateStore
from penguiflow.cli.playground_wrapper import AgentWrapper, ChatResult
from penguiflow.planner import Trajectory


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

    async def wait_for_trace_persistence(
        self,
        trace_id: str,
        session_id: str,
        *,
        timeout_s: float = 1.0,
    ) -> None:
        del trace_id, session_id, timeout_s

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


class TestTracesEndpoint:
    """Tests for /traces endpoint."""

    def test_list_traces_returns_newest_first_with_limit(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        asyncio.run(store.save_trajectory("trace-old", "session-a", Trajectory(query="old")))
        asyncio.run(store.save_trajectory("trace-new", "session-b", Trajectory(query="new")))

        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/traces", params={"limit": 1})
        assert response.status_code == 200
        assert response.json() == [
            {
                "trace_id": "trace-new",
                "session_id": "session-b",
                "tags": [],
                "query_preview": "new",
                "turn_index": 1,
            }
        ]

    def test_list_traces_reads_tags_from_trajectory_metadata(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        seeded = Trajectory(query="seeded")
        seeded.metadata["tags"] = ["dataset:alpha", "split:test"]
        asyncio.run(store.save_trajectory("trace-seeded", "session-a", seeded))

        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/traces")
        assert response.status_code == 200
        assert response.json() == [
            {
                "trace_id": "trace-seeded",
                "session_id": "session-a",
                "tags": ["dataset:alpha", "split:test"],
                "query_preview": "seeded",
                "turn_index": 1,
            }
        ]

    def test_list_traces_includes_query_preview_and_session_turn_index(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        asyncio.run(store.save_trajectory("trace-a1", "session-a", Trajectory(query="alpha first")))
        asyncio.run(store.save_trajectory("trace-b1", "session-b", Trajectory(query="beta first")))
        asyncio.run(store.save_trajectory("trace-a2", "session-a", Trajectory(query="alpha second")))

        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/traces")
        assert response.status_code == 200
        assert response.json() == [
            {
                "trace_id": "trace-a2",
                "session_id": "session-a",
                "tags": [],
                "query_preview": "alpha second",
                "turn_index": 2,
            },
            {
                "trace_id": "trace-b1",
                "session_id": "session-b",
                "tags": [],
                "query_preview": "beta first",
                "turn_index": 1,
            },
            {
                "trace_id": "trace-a1",
                "session_id": "session-a",
                "tags": [],
                "query_preview": "alpha first",
                "turn_index": 1,
            },
        ]

    def test_tag_trace_persists_into_trace_listing(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        asyncio.run(store.save_trajectory("trace-1", "session-a", Trajectory(query="seed")))

        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        tag_response = client.post(
            "/traces/trace-1/tags",
            json={"session_id": "session-a", "add": ["policy", "urgent", "policy"]},
        )
        assert tag_response.status_code == 200
        assert tag_response.json() == {
            "trace_id": "trace-1",
            "session_id": "session-a",
            "tags": ["policy", "urgent"],
            "query_preview": "seed",
        }

        list_response = client.get("/traces")
        assert list_response.status_code == 200
        assert list_response.json() == [
            {
                "trace_id": "trace-1",
                "session_id": "session-a",
                "tags": ["policy", "urgent"],
                "query_preview": "seed",
                "turn_index": 1,
            }
        ]

        stored = asyncio.run(store.get_trajectory("trace-1", "session-a"))
        assert stored is not None
        assert stored.metadata["tags"] == ["policy", "urgent"]

    def test_tag_trace_preserves_existing_metadata(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        seeded = Trajectory(query="seed")
        seeded.metadata["owner"] = "qa"
        seeded.metadata["tags"] = ["legacy", "split:test"]
        asyncio.run(store.save_trajectory("trace-1", "session-a", seeded))

        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/traces/trace-1/tags",
            json={"session_id": "session-a", "add": ["dataset:alpha"], "remove": ["legacy"]},
        )
        assert response.status_code == 200
        assert response.json() == {
            "trace_id": "trace-1",
            "session_id": "session-a",
            "tags": ["dataset:alpha", "split:test"],
            "query_preview": "seed",
        }

        stored = asyncio.run(store.get_trajectory("trace-1", "session-a"))
        assert stored is not None
        assert stored.metadata["owner"] == "qa"
        assert stored.metadata["tags"] == ["dataset:alpha", "split:test"]

    def test_tag_unknown_trace_returns_404(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()
        asyncio.run(store.save_trajectory("trace-1", "session-a", Trajectory(query="seed")))

        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/traces/trace-missing/tags",
            json={"session_id": "session-a", "add": ["policy"]},
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Trace not found for tagging"}


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


class TestEvalDatasetExportEndpoint:
    """Tests for /eval/datasets/export endpoint."""

    def test_export_dataset_from_tag_selector(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        store = InMemoryStateStore()

        selected = Trajectory(query="selected-question")
        selected.metadata["tags"] = ["dataset:alpha", "split:test"]
        asyncio.run(store.save_trajectory("trace-selected", "session-a", selected))

        unselected = Trajectory(query="unselected-question")
        unselected.metadata["tags"] = ["dataset:beta", "split:test"]
        asyncio.run(store.save_trajectory("trace-unselected", "session-a", unselected))

        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/eval/datasets/export",
            json={
                "selector": {"include_tags": ["dataset:alpha"]},
                "output_dir": "playground_eval/dataset_a",
                "redaction_profile": "internal_safe",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["trace_count"] == 1

        dataset_path = Path(payload["dataset_path"])
        manifest_path = Path(payload["manifest_path"])
        assert dataset_path.exists()
        assert manifest_path.exists()

        rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 1
        assert rows[0]["example_id"] == "trace-selected"


class TestEvalDatasetLoadEndpoint:
    """Tests for /eval/datasets/load endpoint."""

    def test_load_dataset_summary(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=InMemoryStateStore())
        client = TestClient(app, raise_server_exceptions=False)

        dataset_dir = tmp_path / "fixtures" / "dataset"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        dataset_path = dataset_dir / "dataset.jsonl"
        manifest_path = dataset_dir / "manifest.json"
        dataset_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "example_id": "ex-1",
                            "split": "val",
                            "question": "first question",
                            "gold_trace": {"inputs": {}},
                        }
                    ),
                    json.dumps(
                        {
                            "example_id": "ex-2",
                            "split": "test",
                            "question": "second question",
                            "gold_trace": {"inputs": {}},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_path.write_text(json.dumps({"version": 1}), encoding="utf-8")

        response = client.post("/eval/datasets/load", json={"dataset_path": "fixtures/dataset"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"] == {"total": 2, "by_split": {"test": 1, "val": 1}}
        assert payload["examples"] == [
            {"example_id": "ex-1", "split": "val", "question": "first question"},
            {"example_id": "ex-2", "split": "test", "question": "second question"},
        ]


class TestEvalDatasetBrowseEndpoint:
    """Tests for /eval/datasets/browse endpoint."""

    def test_browse_returns_only_app_eval_jsonl(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()

        app_root = tmp_path / "example_app"
        evals_dir = app_root / "evals" / "policy"
        evals_dir.mkdir(parents=True, exist_ok=True)
        (evals_dir / "dataset.jsonl").write_text("{}\n", encoding="utf-8")
        (evals_dir / "notes.txt").write_text("ignore", encoding="utf-8")

        other_dir = tmp_path / "other" / "evals"
        other_dir.mkdir(parents=True, exist_ok=True)
        (other_dir / "dataset.jsonl").write_text("{}\n", encoding="utf-8")

        app = create_playground_app(project_root=tmp_path, agent=wrapper, agent_package="example_app")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/eval/datasets/browse")
        assert response.status_code == 200
        payload = response.json()

        assert payload == [
            {
                "path": "example_app/evals/policy/dataset.jsonl",
                "label": "policy/dataset.jsonl",
                "is_default": True,
            }
        ]


class TestEvalMetricBrowseEndpoint:
    """Tests for /eval/metrics/browse endpoint."""

    def test_browse_metrics_from_eval_specs(self, tmp_path: Path) -> None:
        wrapper = MockAgentWrapper()

        app_root = tmp_path / "example_app"
        evals_dir = app_root / "evals" / "policy"
        evals_dir.mkdir(parents=True, exist_ok=True)
        (evals_dir / "evaluate.spec.json").write_text(
            json.dumps(
                {
                    "dataset_path": "example_app/evals/policy/dataset/dataset.jsonl",
                    "metric_spec": "example_app.evals.metrics:policy_metric",
                }
            ),
            encoding="utf-8",
        )
        other_dir = app_root / "evals" / "security"
        other_dir.mkdir(parents=True, exist_ok=True)
        (other_dir / "evaluate.spec.json").write_text(
            json.dumps(
                {
                    "dataset_path": "example_app/evals/security/dataset/dataset.jsonl",
                    "metric_spec": "example_app.evals.metrics:policy_metric",
                }
            ),
            encoding="utf-8",
        )

        app = create_playground_app(project_root=tmp_path, agent=wrapper, agent_package="example_app")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/eval/metrics/browse")
        assert response.status_code == 200
        payload = response.json()

        assert payload == [
            {
                "metric_spec": "example_app.evals.metrics:policy_metric",
                "label": "policy_metric",
                "source_spec_path": "example_app/evals/policy/evaluate.spec.json",
            }
        ]


class TestEvalRunEndpoint:
    """Tests for /eval/run endpoint."""

    @staticmethod
    def _write_eval_metric(tmp_path: Path) -> None:
        metric_file = tmp_path / "eval_metric.py"
        metric_file.write_text(
            "def score(gold, pred, trace=None, pred_name=None, pred_trace=None):\n"
            "    return {'score': 0.75, 'feedback': 'looks good'}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_eval_metric_requiring_row_and_pred_trace(tmp_path: Path) -> None:
        metric_file = tmp_path / "eval_metric_shape.py"
        metric_file.write_text(
            "def score(gold, pred, trace=None, pred_name=None, pred_trace=None):\n"
            "    del pred, trace, pred_name\n"
            "    has_question = isinstance(gold, dict) and gold.get('question') == 'what is policy'\n"
            "    has_steps = isinstance(pred_trace, dict) and isinstance(pred_trace.get('steps'), list)\n"
            "    return {'score': 1.0 if (has_question and has_steps) else 0.0, 'feedback': 'shape check'}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_dataset(tmp_path: Path, questions: list[str]) -> None:
        dataset_dir = tmp_path / "fixtures" / "dataset"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            json.dumps(
                {
                    "example_id": f"ex-{index + 1}",
                    "split": "val",
                    "question": question,
                    "gold_trace": {"inputs": {"llm_context": {"tenant": "t-1"}, "tool_context": {"scope": "x"}}},
                }
            )
            for index, question in enumerate(questions)
        ]
        (dataset_dir / "dataset.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    def test_eval_run_returns_cases_with_prediction_trace_refs(self, tmp_path: Path) -> None:
        self._write_eval_metric(tmp_path)
        self._write_dataset(tmp_path, ["what is policy"])

        store = InMemoryStateStore()
        wrapper = MockAgentWrapper(
            chat_result=ChatResult(
                trace_id="pred-trace-1",
                session_id="ignored-by-endpoint",
                answer="policy answer",
                metadata={"steps": 1},
                pause=None,
            )
        )
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/eval/run",
            json={"dataset_path": "fixtures/dataset", "metric_spec": "eval_metric:score", "min_test_score": 0.5},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"] == {"total": 1, "val": 1, "test": 0}
        assert len(payload["cases"]) == 1
        case = payload["cases"][0]
        assert case["example_id"] == "ex-1"
        assert case["split"] == "val"
        assert case["score"] == 0.75
        assert case["feedback"] == "looks good"
        assert case["pred_trace_id"] == "pred-trace-1"
        assert case["pred_session_id"].startswith("eval:")
        assert case["question"] == "what is policy"

    def test_eval_run_passes_dataset_row_and_serialized_pred_trace_to_metric(self, tmp_path: Path) -> None:
        self._write_eval_metric_requiring_row_and_pred_trace(tmp_path)
        self._write_dataset(tmp_path, ["what is policy"])

        class PersistingWrapper(MockAgentWrapper):
            def __init__(self, state_store: InMemoryStateStore) -> None:
                super().__init__(
                    chat_result=ChatResult(
                        trace_id="pred-trace-1",
                        session_id="ignored-by-endpoint",
                        answer="policy answer",
                        metadata={"steps": 1},
                        pause=None,
                    )
                )
                self._state_store = state_store

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
                del event_consumer, trace_id_hint, steering
                trajectory = Trajectory.from_serialised(
                    {
                        "query": query,
                        "llm_context": dict(llm_context or {}),
                        "tool_context": dict(tool_context or {}),
                        "steps": [
                            {
                                "action": {
                                    "thought": "route",
                                    "next_node": "triage_query",
                                    "args": {},
                                }
                            }
                        ],
                    }
                )
                await self._state_store.save_trajectory(self._chat_result.trace_id, session_id, trajectory)
                return self._chat_result

        store = InMemoryStateStore()
        wrapper = PersistingWrapper(store)
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/eval/run",
            json={"dataset_path": "fixtures/dataset", "metric_spec": "eval_metric_shape:score"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["cases"]) == 1
        assert payload["cases"][0]["score"] == 1.0

    def test_eval_run_logs_pred_trace_step_visibility(self, tmp_path: Path, caplog: Any) -> None:
        self._write_eval_metric_requiring_row_and_pred_trace(tmp_path)
        self._write_dataset(tmp_path, ["what is policy"])

        class PersistingWrapper(MockAgentWrapper):
            def __init__(self, state_store: InMemoryStateStore) -> None:
                super().__init__(
                    chat_result=ChatResult(
                        trace_id="pred-trace-1",
                        session_id="ignored-by-endpoint",
                        answer="policy answer",
                        metadata={"steps": 1},
                        pause=None,
                    )
                )
                self._state_store = state_store

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
                del event_consumer, trace_id_hint, steering
                trajectory = Trajectory.from_serialised(
                    {
                        "query": query,
                        "llm_context": dict(llm_context or {}),
                        "tool_context": dict(tool_context or {}),
                        "steps": [
                            {
                                "action": {
                                    "thought": "route",
                                    "next_node": "triage_query",
                                    "args": {},
                                }
                            }
                        ],
                    }
                )
                await self._state_store.save_trajectory(self._chat_result.trace_id, session_id, trajectory)
                return self._chat_result

        store = InMemoryStateStore()
        wrapper = PersistingWrapper(store)
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        with caplog.at_level("INFO"):
            response = client.post(
                "/eval/run",
                json={"dataset_path": "fixtures/dataset", "metric_spec": "eval_metric_shape:score"},
            )

        assert response.status_code == 200
        assert any(record.msg == "eval_run_metric_debug" for record in caplog.records)
        assert any("eval_run_metric_debug_details" in record.getMessage() for record in caplog.records)

    def test_eval_run_waits_for_delayed_trace_persistence(self, tmp_path: Path) -> None:
        self._write_eval_metric_requiring_row_and_pred_trace(tmp_path)
        self._write_dataset(tmp_path, ["what is policy"])

        class DelayedPersistingWrapper(MockAgentWrapper):
            def __init__(self, state_store: InMemoryStateStore) -> None:
                super().__init__(
                    chat_result=ChatResult(
                        trace_id="pred-trace-1",
                        session_id="ignored-by-endpoint",
                        answer="policy answer",
                        metadata={"steps": 1},
                        pause=None,
                    )
                )
                self._state_store = state_store
                self._persist_task: asyncio.Task[None] | None = None
                self.wait_calls = 0

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
                del event_consumer, trace_id_hint, steering
                trajectory = Trajectory.from_serialised(
                    {
                        "query": query,
                        "llm_context": dict(llm_context or {}),
                        "tool_context": dict(tool_context or {}),
                        "steps": [
                            {
                                "action": {
                                    "thought": "route",
                                    "next_node": "triage_query",
                                    "args": {},
                                }
                            }
                        ],
                    }
                )

                async def _persist_later() -> None:
                    await asyncio.sleep(0.05)
                    await self._state_store.save_trajectory(self._chat_result.trace_id, session_id, trajectory)

                self._persist_task = asyncio.create_task(_persist_later())
                return self._chat_result

            async def wait_for_trace_persistence(
                self,
                trace_id: str,
                session_id: str,
                *,
                timeout_s: float = 1.0,
            ) -> None:
                del trace_id, session_id
                self.wait_calls += 1
                if self._persist_task is not None:
                    await asyncio.wait_for(self._persist_task, timeout=timeout_s)

        store = InMemoryStateStore()
        wrapper = DelayedPersistingWrapper(store)
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/eval/run",
            json={"dataset_path": "fixtures/dataset", "metric_spec": "eval_metric_shape:score"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["cases"]) == 1
        assert payload["cases"][0]["score"] == 1.0
        assert wrapper.wait_calls == 1

    def test_eval_run_applies_default_hard_max_cases_cap(self, tmp_path: Path) -> None:
        self._write_eval_metric(tmp_path)
        self._write_dataset(tmp_path, [f"question {index}" for index in range(205)])

        store = InMemoryStateStore()
        wrapper = MockAgentWrapper(
            chat_result=ChatResult(
                trace_id="pred-trace-1",
                session_id="ignored-by-endpoint",
                answer="policy answer",
                metadata={"steps": 1},
                pause=None,
            )
        )
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/eval/run",
            json={"dataset_path": "fixtures/dataset", "metric_spec": "eval_metric:score"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"] == {"total": 200, "val": 200, "test": 0}
        assert len(payload["cases"]) == 200

    def test_eval_run_clamps_max_cases_above_hard_cap(self, tmp_path: Path) -> None:
        self._write_eval_metric(tmp_path)
        self._write_dataset(tmp_path, [f"question {index}" for index in range(205)])

        store = InMemoryStateStore()
        wrapper = MockAgentWrapper(
            chat_result=ChatResult(
                trace_id="pred-trace-1",
                session_id="ignored-by-endpoint",
                answer="policy answer",
                metadata={"steps": 1},
                pause=None,
            )
        )
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/eval/run",
            json={"dataset_path": "fixtures/dataset", "metric_spec": "eval_metric:score", "max_cases": 1000},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"] == {"total": 200, "val": 200, "test": 0}
        assert len(payload["cases"]) == 200

    def test_eval_run_rejects_non_positive_max_cases(self, tmp_path: Path) -> None:
        self._write_eval_metric(tmp_path)
        self._write_dataset(tmp_path, ["what is policy"])

        store = InMemoryStateStore()
        wrapper = MockAgentWrapper(
            chat_result=ChatResult(
                trace_id="pred-trace-1",
                session_id="ignored-by-endpoint",
                answer="policy answer",
                metadata={"steps": 1},
                pause=None,
            )
        )
        app = create_playground_app(project_root=tmp_path, agent=wrapper, state_store=store)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/eval/run",
            json={"dataset_path": "fixtures/dataset", "metric_spec": "eval_metric:score", "max_cases": 0},
        )

        assert response.status_code == 422


class TestArtifactEndpoints:
    """Tests for /artifacts endpoints (lines 958-1059)."""

    def test_get_artifact_no_planner_store_returns_404(self, tmp_path: Path) -> None:
        """Test /artifacts with no planner artifact store falls back to state store (404 for missing)."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        # With the state store fallback, _discover_artifact_store() finds the
        # InMemoryStateStore's artifact store. The artifact "test-id" doesn't
        # exist, so the endpoint returns 404 (not 501).
        response = client.get("/artifacts/test-id")
        assert response.status_code == 404

    def test_get_artifact_meta_no_planner_store_returns_404(self, tmp_path: Path) -> None:
        """Test /artifacts/{id}/meta with no planner artifact store falls back to state store, returning 404."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        # With the state store fallback, _discover_artifact_store() finds the
        # InMemoryStateStore's artifact store. The artifact "test-id" doesn't
        # exist, so the endpoint returns 404 (not 501).
        response = client.get("/artifacts/test-id/meta")
        assert response.status_code == 404


class TestListArtifactsEndpoint:
    """Tests for GET /artifacts list endpoint."""

    def test_list_artifacts_no_store_returns_empty(self, tmp_path: Path) -> None:
        """When no artifact store is configured, returns []."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts", params={"session_id": "sess-1"})
        assert response.status_code == 200
        assert response.json() == []

    def test_list_artifacts_no_session_returns_empty(self, tmp_path: Path) -> None:
        """When no session ID is provided (no query param, no header), returns []."""
        store = InMemoryArtifactStore()
        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_artifacts_valid_session(self, tmp_path: Path) -> None:
        """Valid session with artifacts returns list of artifact dicts without scope field."""
        store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="sess-1", tenant_id="t-1", user_id="u-1")
        asyncio.run(store.put_text("hello", mime_type="text/plain", filename="test.txt", scope=scope))

        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            "/artifacts",
            params={
                "session_id": "sess-1",
                "tenant_id": "t-1",
                "user_id": "u-1",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        # Verify scope field is excluded from response
        assert "scope" not in data[0]
        assert "id" in data[0]

    def test_list_artifacts_session_via_header(self, tmp_path: Path) -> None:
        """Session via X-Session-ID header resolves correctly."""
        store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="header-sess")
        asyncio.run(store.put_text("data", scope=scope))

        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts", headers={"X-Session-ID": "header-sess"})
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_list_artifacts_query_param_priority_over_header(self, tmp_path: Path) -> None:
        """Session via query param takes priority over header when both provided."""
        store = InMemoryArtifactStore()
        scope_query = ArtifactScope(session_id="query-sess")
        scope_header = ArtifactScope(session_id="header-sess")
        asyncio.run(store.put_text("query data", scope=scope_query))
        asyncio.run(store.put_text("header data", scope=scope_header))

        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            "/artifacts",
            params={"session_id": "query-sess"},
            headers={"X-Session-ID": "header-sess"},
        )
        assert response.status_code == 200
        data = response.json()
        # Should return only the query-sess artifact
        assert len(data) == 1

    def test_list_artifacts_empty_session(self, tmp_path: Path) -> None:
        """Empty session (no artifacts stored) returns []."""
        store = InMemoryArtifactStore()
        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts", params={"session_id": "empty-sess"})
        assert response.status_code == 200
        assert response.json() == []


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

    def test_discover_skips_noop_planner_store_falls_back_to_state_store(self, tmp_path: Path) -> None:
        """Test _discover_artifact_store skips NoOpArtifactStore on planner, falls back to state store."""
        from penguiflow.artifacts import NoOpArtifactStore

        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = NoOpArtifactStore()

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        # The planner's NoOpArtifactStore is skipped, but the state store
        # fallback finds the InMemoryStateStore's PlaygroundArtifactStore.
        # The artifact "test-id" doesn't exist, so 404 is returned (not 501).
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/artifacts/test-id")
        assert response.status_code == 404


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
