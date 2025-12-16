"""Tests for penguiflow/planner/trajectory.py edge cases."""

from pydantic import BaseModel

from penguiflow.planner.models import PlannerAction
from penguiflow.planner.trajectory import Trajectory, TrajectoryStep, TrajectorySummary

# ─── TrajectorySummary tests ─────────────────────────────────────────────────


def test_trajectory_summary_compact_with_note():
    """compact() should include note when present."""
    summary = TrajectorySummary(
        goals=["goal1"],
        facts={"key": "value"},
        pending=["task1"],
        last_output_digest="digest",
        note="important note",
    )
    result = summary.compact()

    assert result["goals"] == ["goal1"]
    assert result["note"] == "important note"


def test_trajectory_summary_compact_without_note():
    """compact() should exclude note when None."""
    summary = TrajectorySummary(goals=["goal1"])
    result = summary.compact()

    assert "note" not in result


# ─── TrajectoryStep tests ────────────────────────────────────────────────────


def test_trajectory_step_dump_with_llm_observation():
    """dump() should include llm_observation when present."""
    action = PlannerAction(thought="test thought", next_node="test_node")
    step = TrajectoryStep(
        action=action,
        observation={"result": "data"},
        llm_observation={"redacted": "data"},
    )
    result = step.dump()

    assert result["llm_observation"] == {"redacted": "data"}


def test_trajectory_step_dump_with_failure():
    """dump() should include failure when present."""
    action = PlannerAction(thought="test thought", next_node="test_node")
    step = TrajectoryStep(
        action=action,
        failure={"error": "details", "traceback": "stack"},
    )
    result = step.dump()

    assert result["failure"] == {"error": "details", "traceback": "stack"}


def test_trajectory_step_dump_with_streams():
    """dump() should include streams when present."""
    action = PlannerAction(thought="test thought", next_node="test_node")
    step = TrajectoryStep(
        action=action,
        streams={
            "stream1": [{"text": "chunk1"}, {"text": "chunk2"}],
        },
    )
    result = step.dump()

    assert "streams" in result
    assert result["streams"]["stream1"] == [{"text": "chunk1"}, {"text": "chunk2"}]


def test_trajectory_step_serialize_observation_basemodel():
    """_serialise_observation should handle BaseModel."""
    action = PlannerAction(thought="test thought", next_node="test_node")

    class MyModel(BaseModel):
        data: str
        count: int

    step = TrajectoryStep(
        action=action,
        observation=MyModel(data="test", count=42),
    )
    result = step._serialise_observation()

    assert result == {"data": "test", "count": 42}


def test_trajectory_step_serialise_for_llm_with_llm_observation():
    """serialise_for_llm should prefer llm_observation."""
    action = PlannerAction(thought="test thought", next_node="test_node")
    step = TrajectoryStep(
        action=action,
        observation={"full": "data"},
        llm_observation={"redacted": "data"},
    )
    result = step.serialise_for_llm()

    assert result == {"redacted": "data"}


def test_trajectory_step_serialise_for_llm_without_llm_observation():
    """serialise_for_llm should fallback to observation."""
    action = PlannerAction(thought="test thought", next_node="test_node")
    step = TrajectoryStep(
        action=action,
        observation={"full": "data"},
    )
    result = step.serialise_for_llm()

    assert result == {"full": "data"}


# ─── Trajectory tests ────────────────────────────────────────────────────────


def test_trajectory_serialise_with_nonserializable_tool_context():
    """serialise should handle non-JSON-serializable tool_context."""
    trajectory = Trajectory(
        query="test query",
        tool_context={"func": lambda x: x},  # Not JSON serializable
    )
    result = trajectory.serialise()

    # Should gracefully handle and set to None
    assert result["tool_context"] is None


def test_trajectory_serialise_with_summary():
    """serialise should include summary when present."""
    trajectory = Trajectory(query="test query")
    trajectory.summary = TrajectorySummary(goals=["goal1"])
    result = trajectory.serialise()

    assert result["summary"] is not None
    assert result["summary"]["goals"] == ["goal1"]


def test_trajectory_from_serialised_with_summary():
    """from_serialised should restore summary."""
    payload = {
        "query": "test",
        "steps": [],
        "summary": {"goals": ["restored goal"], "facts": {}, "pending": []},
    }
    trajectory = Trajectory.from_serialised(payload)

    assert trajectory.summary is not None
    assert trajectory.summary.goals == ["restored goal"]


def test_trajectory_from_serialised_with_sources():
    """from_serialised should restore sources."""
    payload = {
        "query": "test",
        "steps": [],
        "sources": [{"url": "http://example.com", "title": "Example"}],
    }
    trajectory = Trajectory.from_serialised(payload)

    assert len(trajectory.sources) == 1
    assert trajectory.sources[0]["url"] == "http://example.com"


def test_trajectory_from_serialised_with_invalid_sources():
    """from_serialised should skip invalid sources."""
    payload = {
        "query": "test",
        "steps": [],
        "sources": ["not_a_mapping", {"valid": "source"}],
    }
    trajectory = Trajectory.from_serialised(payload)

    assert len(trajectory.sources) == 1
    assert trajectory.sources[0]["valid"] == "source"


def test_trajectory_from_serialised_with_streams():
    """from_serialised should restore streams in steps."""
    payload = {
        "query": "test",
        "steps": [
            {
                "action": {"thought": "test", "next_node": "node"},
                "streams": {
                    "stream1": [{"text": "chunk1"}, {"text": "chunk2"}],
                },
            }
        ],
    }
    trajectory = Trajectory.from_serialised(payload)

    assert len(trajectory.steps) == 1
    assert trajectory.steps[0].streams is not None
    assert "stream1" in trajectory.steps[0].streams


def test_trajectory_from_serialised_with_invalid_stream_chunks():
    """from_serialised should skip invalid stream chunks."""
    payload = {
        "query": "test",
        "steps": [
            {
                "action": {"thought": "test", "next_node": "node"},
                "streams": {
                    "stream1": "not_a_sequence",  # Invalid
                },
            }
        ],
    }
    trajectory = Trajectory.from_serialised(payload)

    # Should have empty streams dict since the stream was invalid
    assert trajectory.steps[0].streams == {} or trajectory.steps[0].streams is None


def test_trajectory_from_serialised_with_legacy_context_meta():
    """from_serialised should handle legacy context_meta field."""
    payload = {
        "query": "test",
        "context_meta": {"legacy": "context"},
        "steps": [],
    }
    trajectory = Trajectory.from_serialised(payload)

    assert trajectory.llm_context == {"legacy": "context"}


def test_trajectory_from_serialised_with_invalid_tool_context():
    """from_serialised should handle non-mapping tool_context."""
    payload = {
        "query": "test",
        "tool_context": "not_a_mapping",
        "steps": [],
    }
    trajectory = Trajectory.from_serialised(payload)

    assert trajectory.tool_context == {}


def test_trajectory_compress_with_error():
    """compress should capture last error."""
    trajectory = Trajectory(query="test query")
    action = PlannerAction(thought="test", next_node="failing_node")
    trajectory.steps.append(
        TrajectoryStep(action=action, error="Something failed")
    )

    summary = trajectory.compress()

    assert "last_error" in summary.facts
    assert summary.facts["last_error"] == "Something failed"


def test_trajectory_compress_with_retry_pending():
    """compress should add pending retry for error steps."""
    trajectory = Trajectory(query="test query")
    action = PlannerAction(thought="test", next_node="node_to_retry")
    trajectory.steps.append(TrajectoryStep(action=action, error="Error occurred"))

    summary = trajectory.compress()

    assert "retry node_to_retry" in summary.pending


def test_trajectory_compress_with_observation():
    """compress should capture last observation digest."""
    trajectory = Trajectory(query="test query")
    action = PlannerAction(thought="test", next_node="test_node")
    trajectory.steps.append(
        TrajectoryStep(action=action, observation={"result": "data"})
    )

    summary = trajectory.compress()

    assert summary.last_output_digest is not None
    assert "result" in summary.last_output_digest


def test_trajectory_compress_with_long_observation():
    """compress should truncate long observation digest."""
    trajectory = Trajectory(query="test query")
    action = PlannerAction(thought="test", next_node="test_node")
    # Create observation that will produce > 120 char digest
    long_data = {"result": "x" * 200}
    trajectory.steps.append(TrajectoryStep(action=action, observation=long_data))

    summary = trajectory.compress()

    assert summary.last_output_digest is not None
    assert summary.last_output_digest.endswith("...")
    assert len(summary.last_output_digest) <= 120
