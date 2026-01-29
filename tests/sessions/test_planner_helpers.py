from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from penguiflow.artifacts import ArtifactRef
from penguiflow.sessions.planner import (
    _build_context_patch,
    _extract_answer,
    _is_artifact_ref_dict,
    _normalize_artifacts_for_patch,
    _serialize_artifact_value,
)


@dataclass(frozen=True, slots=True)
class _AnswerObj:
    answer: str


class _DummyModel(BaseModel):
    value: int


def test_is_artifact_ref_dict() -> None:
    assert _is_artifact_ref_dict({"id": "abc"})
    assert _is_artifact_ref_dict({"id": "abc", "mime_type": "text/plain"})
    assert not _is_artifact_ref_dict({"id": 123})
    assert not _is_artifact_ref_dict({"mime_type": "text/plain"})
    assert not _is_artifact_ref_dict("nope")


def test_serialize_artifact_value() -> None:
    ref = ArtifactRef(id="artifact-1", mime_type="text/plain")
    serialized = _serialize_artifact_value(ref)
    assert isinstance(serialized, dict)
    assert serialized["id"] == "artifact-1"
    assert serialized["mime_type"] == "text/plain"
    assert _serialize_artifact_value(_DummyModel(value=1)) == {"value": 1}
    assert _serialize_artifact_value({"id": "artifact-2"}) == {"id": "artifact-2"}
    assert _serialize_artifact_value({"k": "v"}) == {"k": "v"}
    assert _serialize_artifact_value("raw") is None


def test_normalize_artifacts_for_patch_handles_dict_and_list() -> None:
    ref = ArtifactRef(id="artifact-3", mime_type="application/pdf")
    artifacts = {
        "node_a": {"file": ref, "raw": {"id": "artifact-4"}},
        "node_b": {"not_artifact": "skip"},
        "node_c": {"data": {"k": "v"}},
        "node_d": ref,
    }
    normalized = _normalize_artifacts_for_patch(artifacts)
    assert any(
        item.get("node") == "node_a"
        and item.get("field") == "file"
        and item.get("artifact", {}).get("id") == "artifact-3"
        for item in normalized
    )
    assert {"node": "node_a", "field": "raw", "artifact": {"id": "artifact-4"}} in normalized
    assert {"node": "node_c", "field": "data", "artifact": {"k": "v"}} in normalized
    assert any(
        item.get("node") == "node_d" and item.get("artifact", {}).get("id") == "artifact-3"
        for item in normalized
    )
    assert all(item.get("field") != "not_artifact" for item in normalized)

    assert _normalize_artifacts_for_patch([{"ok": True}, "skip", {"node": "x"}]) == [{"ok": True}, {"node": "x"}]
    assert _normalize_artifacts_for_patch("nope") == []
    assert _normalize_artifacts_for_patch(None) == []


def test_extract_answer_variants() -> None:
    assert _extract_answer("hi") == "hi"
    assert _extract_answer({"answer": "yes"}) == "yes"
    assert _extract_answer({"raw_answer": 123}) == "123"
    assert _extract_answer(_AnswerObj(answer="attr")) == "attr"
    assert _extract_answer({"answer": None, "other": "x"}) is None
    assert _extract_answer(None) is None


def test_build_context_patch_includes_digest_artifacts_sources() -> None:
    ref = ArtifactRef(id="artifact-5", mime_type="text/plain")
    patch = _build_context_patch(
        task_id="t1",
        payload={"answer": "hello"},
        metadata={"artifacts": {"node": {"file": ref}}, "sources": [{"id": "s1"}]},
        context_version=2,
        context_hash="h",
        spawned_from_event_id="e1",
    )
    assert patch.task_id == "t1"
    assert patch.spawned_from_event_id == "e1"
    assert patch.source_context_version == 2
    assert patch.source_context_hash == "h"
    assert patch.digest == ["hello"]
    assert patch.sources == [{"id": "s1"}]
    assert patch.artifacts
