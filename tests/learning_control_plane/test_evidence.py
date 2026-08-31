from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from learning_control_plane.contracts import EvidenceContext, EvidenceEvent
from learning_control_plane.evidence import CompositeEvidenceSink, MlflowEvidenceSink, OpenTelemetryEvidenceSink


def _event(**attributes: Any) -> EvidenceEvent:
    return EvidenceEvent(
        event_type="evaluation.completed",
        context=EvidenceContext(
            agent_id="support-agent",
            deployment_digest="sha256:agent-v1",
            trace_id="trace-1",
            evaluation_id="eval-1",
            candidate_id="candidate-1",
        ),
        attributes=attributes,
    )


def test_evidence_event_redacts_sensitive_fields() -> None:
    event = _event(score=0.9, prompt="do not export", api_key="do not export")

    assert event.attributes == {"score": 0.9}
    assert "prompt" not in event.telemetry_attributes()
    assert event.record()["attributes"] == {"score": 0.9}


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class _FakeTracer:
    def __init__(self) -> None:
        self.name: str | None = None
        self.span = _FakeSpan()

    @contextmanager
    def start_as_current_span(self, name: str):
        self.name = name
        yield self.span


def test_open_telemetry_sink_emits_only_redacted_metadata() -> None:
    tracer = _FakeTracer()

    assert OpenTelemetryEvidenceSink(tracer=tracer).emit(_event(score=0.9, content="private"))

    assert tracer.name == "lcp.evidence.evaluation.completed"
    assert tracer.span.attributes["lcp.agent_id"] == "support-agent"
    assert tracer.span.attributes["lcp.attr.score"] == 0.9
    assert all("content" not in key for key in tracer.span.attributes)


class _FakeMlflowRun:
    def __enter__(self) -> _FakeMlflowRun:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _FakeMlflow:
    def __init__(self) -> None:
        self.tags: dict[str, str] = {}
        self.dicts: dict[str, dict[str, Any]] = {}
        self.run_names: list[str] = []

    def active_run(self) -> None:
        return None

    def start_run(self, *, run_name: str) -> _FakeMlflowRun:
        self.run_names.append(run_name)
        return _FakeMlflowRun()

    def set_tags(self, tags: dict[str, str]) -> None:
        self.tags.update(tags)

    def set_tag(self, key: str, value: str) -> None:
        self.tags[key] = value

    def log_dict(self, value: dict[str, Any], path: str) -> None:
        self.dicts[path] = value


def test_mlflow_sink_records_redacted_event_and_lineage() -> None:
    mlflow = _FakeMlflow()
    event = _event(score=0.9, token="private")

    assert MlflowEvidenceSink(mlflow_module=mlflow).emit(event)

    assert mlflow.run_names == ["lcp-evidence-evaluation.completed"]
    assert mlflow.tags["lcp.event_id"] == event.event_id
    assert mlflow.tags["lcp.agent_id"] == "support-agent"
    assert next(iter(mlflow.dicts.values()))["attributes"] == {"score": 0.9}


class _FailingSink:
    def emit(self, event: EvidenceEvent) -> bool:
        raise RuntimeError("unavailable")


class _SuccessfulSink:
    def emit(self, event: EvidenceEvent) -> bool:
        return True


def test_composite_sink_continues_after_a_failed_sink() -> None:
    sink = CompositeEvidenceSink([_FailingSink(), _SuccessfulSink()])

    assert sink.emit(_event())
