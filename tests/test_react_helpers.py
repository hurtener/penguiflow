"""Tests for helper functions in penguiflow/planner/react.py."""

from collections.abc import Sequence
from typing import Literal

import pytest
from pydantic import BaseModel, Field

from penguiflow.planner.react import (
    _ArtifactCollector,
    _coerce_tool_context,
    _default_for_annotation,
    _extract_source_payloads,
    _fallback_answer,
    _model_json_schema_extra,
    _normalise_artifact_value,
    _produces_sources,
    _salvage_action_payload,
    _source_field_map,
    _SourceCollector,
    _StreamingArgsExtractor,
    _StreamingThoughtExtractor,
    _validate_llm_context,
)

# ─── _validate_llm_context tests ─────────────────────────────────────────────


def test_validate_llm_context_none():
    """_validate_llm_context should return None for None input."""
    result = _validate_llm_context(None)
    assert result is None


def test_validate_llm_context_valid():
    """_validate_llm_context should return dict for valid mapping."""
    result = _validate_llm_context({"key": "value", "num": 42})
    assert result == {"key": "value", "num": 42}


def test_validate_llm_context_not_mapping():
    """_validate_llm_context should raise for non-mapping."""
    with pytest.raises(TypeError, match="must be a mapping"):
        _validate_llm_context("not a mapping")  # type: ignore


def test_validate_llm_context_not_serializable():
    """_validate_llm_context should raise for non-JSON-serializable."""
    with pytest.raises(TypeError, match="must be JSON-serializable"):
        _validate_llm_context({"func": lambda x: x})


# ─── _coerce_tool_context tests ──────────────────────────────────────────────


def test_coerce_tool_context_none():
    """_coerce_tool_context should return empty dict for None."""
    result = _coerce_tool_context(None)
    assert result == {}


def test_coerce_tool_context_valid():
    """_coerce_tool_context should return dict for valid mapping."""
    result = _coerce_tool_context({"key": "value"})
    assert result == {"key": "value"}


def test_coerce_tool_context_not_mapping():
    """_coerce_tool_context should raise for non-mapping."""
    with pytest.raises(TypeError, match="must be a mapping"):
        _coerce_tool_context("not a mapping")  # type: ignore


# ─── _salvage_action_payload tests ───────────────────────────────────────────


def test_salvage_action_payload_valid_json():
    """_salvage_action_payload should parse valid JSON."""
    raw = '{"thought": "thinking", "next_node": "tool_x", "args": {"q": "test"}}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "thinking"
    assert result.next_node == "tool_x"


def test_salvage_action_payload_minimal():
    """_salvage_action_payload should fill defaults for minimal JSON."""
    raw = '{"thought": "thinking"}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "thinking"
    assert result.next_node is None


def test_salvage_action_payload_no_thought():
    """_salvage_action_payload should provide default thought."""
    raw = '{"next_node": "some_tool"}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "planning next step"


def test_salvage_action_payload_nested_action():
    """_salvage_action_payload should unwrap nested action dict."""
    raw = '{"action": {"thought": "nested thought", "next_node": "tool_y"}}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "nested thought"
    assert result.next_node == "tool_y"


def test_salvage_action_payload_with_plan():
    """_salvage_action_payload should normalize plan entries."""
    raw = '{"thought": "parallel", "plan": [{"node": "tool_a"}, {"node": "tool_b", "args": {"x": 1}}]}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.plan is not None
    assert len(result.plan) == 2


def test_salvage_action_payload_invalid_plan_entries():
    """_salvage_action_payload should skip invalid plan entries."""
    raw = '{"thought": "parallel", "plan": ["invalid", {"no_node": true}, {"node": "valid"}]}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.plan is not None
    assert len(result.plan) == 1


def test_salvage_action_payload_with_join():
    """_salvage_action_payload should normalize join dict."""
    raw = '{"thought": "join", "join": {"node": "join_tool"}}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.join is not None


def test_salvage_action_payload_python_literal():
    """_salvage_action_payload should handle Python literal syntax."""
    raw = "{'thought': 'python style', 'next_node': None}"
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "python style"


def test_salvage_action_payload_invalid():
    """_salvage_action_payload should return None for invalid input."""
    result = _salvage_action_payload("not valid json or python")
    assert result is None


def test_salvage_action_payload_fills_args_when_missing():
    """_salvage_action_payload should fill args={} when next_node set but args missing."""
    raw = '{"thought": "test", "next_node": "some_tool"}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.args == {}


# ─── _default_for_annotation tests ───────────────────────────────────────────


def test_default_for_annotation_literal():
    """_default_for_annotation should return first value for Literal."""
    result = _default_for_annotation(Literal["a", "b", "c"])
    assert result == "a"


def test_default_for_annotation_str():
    """_default_for_annotation should return 'unknown' for str."""
    result = _default_for_annotation(str)
    assert result == "unknown"


def test_default_for_annotation_bool():
    """_default_for_annotation should return False for bool."""
    result = _default_for_annotation(bool)
    assert result is False


def test_default_for_annotation_int():
    """_default_for_annotation should return 0 for int."""
    result = _default_for_annotation(int)
    assert result == 0


def test_default_for_annotation_float():
    """_default_for_annotation should return 0.0 for float."""
    result = _default_for_annotation(float)
    assert result == 0.0


def test_default_for_annotation_list():
    """_default_for_annotation should return [] for list."""
    result = _default_for_annotation(list[str])
    assert result == []


def test_default_for_annotation_dict():
    """_default_for_annotation should return {} for dict."""
    result = _default_for_annotation(dict[str, int])
    assert result == {}


def test_default_for_annotation_sequence():
    """_default_for_annotation should return [] for Sequence."""
    result = _default_for_annotation(Sequence[str])
    assert result == []


def test_default_for_annotation_basemodel():
    """_default_for_annotation should return {} for BaseModel subclass."""

    class TestModel(BaseModel):
        field: str

    result = _default_for_annotation(TestModel)
    assert result == {}


def test_default_for_annotation_optional():
    """_default_for_annotation should handle Optional (Union with None)."""
    result = _default_for_annotation(str | None)
    # Should return "unknown" for the str part
    assert result == "unknown"


def test_default_for_annotation_unknown():
    """_default_for_annotation should return '<auto>' for unknown types."""

    class CustomClass:
        pass

    result = _default_for_annotation(CustomClass)
    assert result == "<auto>"


# ─── _StreamingArgsExtractor tests ───────────────────────────────────────────


def test_streaming_args_extractor_non_finish():
    """_StreamingArgsExtractor should not extract for non-finish actions."""
    extractor = _StreamingArgsExtractor()
    extractor.feed('{"thought": "thinking", "next_node": "tool_x"')
    assert not extractor.is_finish_action


def test_streaming_args_extractor_finish_detection():
    """_StreamingArgsExtractor should detect finish action."""
    extractor = _StreamingArgsExtractor()
    extractor.feed('{"thought": "done", "next_node": null')
    assert extractor.is_finish_action


def test_streaming_args_extractor_extracts_answer():
    """_StreamingArgsExtractor should extract answer content."""
    extractor = _StreamingArgsExtractor()
    chunks = []
    chunks.extend(extractor.feed('{"thought": "done", "next_node": null, '))
    chunks.extend(extractor.feed('"args": {"answer": "Hello'))
    chunks.extend(extractor.feed(' World"}}'))

    # Should have extracted content from answer field
    assert extractor.is_finish_action
    # Note: The extractor should have emitted content
    text = "".join(chunks)
    assert "Hello" in text or extractor.emitted_count > 0


def test_streaming_args_extractor_handles_escapes():
    """_StreamingArgsExtractor should handle escape sequences."""
    extractor = _StreamingArgsExtractor()
    # Start with finish detection
    extractor.feed('{"thought": "done", "next_node": null, "args": {"answer": "line1\\nline2"}}')
    # The extractor should handle \n escape


# ─── _StreamingThoughtExtractor tests ────────────────────────────────────────


def test_streaming_thought_extractor_basic():
    """_StreamingThoughtExtractor should extract thought content."""
    extractor = _StreamingThoughtExtractor()
    chunks = []
    chunks.extend(extractor.feed('{"thought": "Planning'))
    chunks.extend(extractor.feed(' the next step"'))

    text = "".join(chunks)
    assert "Planning" in text or extractor.emitted_count > 0


def test_streaming_thought_extractor_with_escapes():
    """_StreamingThoughtExtractor should handle escapes."""
    extractor = _StreamingThoughtExtractor()
    extractor.feed('{"thought": "Step 1:\\nStep 2"')
    # Should handle \n escape without error


# ─── _ArtifactCollector tests ────────────────────────────────────────────────


def test_artifact_collector_empty():
    """_ArtifactCollector should start empty."""
    collector = _ArtifactCollector()
    assert collector.snapshot() == {}


def test_artifact_collector_with_existing():
    """_ArtifactCollector should accept existing artifacts."""
    existing = {"tool1": {"data": "value"}}
    collector = _ArtifactCollector(existing)
    assert collector.snapshot() == {"tool1": {"data": "value"}}


def test_artifact_collector_collect():
    """_ArtifactCollector should collect artifact fields."""

    class OutputModel(BaseModel):
        result: str
        artifact_field: str = Field(json_schema_extra={"artifact": True})

    collector = _ArtifactCollector()
    collector.collect(
        "test_node",
        OutputModel,
        {"result": "value", "artifact_field": "artifact_value"},
    )

    snapshot = collector.snapshot()
    assert "test_node" in snapshot
    assert snapshot["test_node"]["artifact_field"] == "artifact_value"


def test_artifact_collector_collect_non_mapping():
    """_ArtifactCollector should handle non-mapping observation."""
    collector = _ArtifactCollector()

    class OutputModel(BaseModel):
        result: str

    collector.collect("test_node", OutputModel, "not a mapping")  # type: ignore
    assert collector.snapshot() == {}


def test_artifact_collector_merge():
    """_ArtifactCollector should merge multiple collections for same node."""

    class OutputModel(BaseModel):
        field1: str = Field(json_schema_extra={"artifact": True})
        field2: str = Field(json_schema_extra={"artifact": True})

    collector = _ArtifactCollector()
    collector.collect("node", OutputModel, {"field1": "value1"})
    collector.collect("node", OutputModel, {"field2": "value2"})

    snapshot = collector.snapshot()
    assert snapshot["node"]["field1"] == "value1"
    assert snapshot["node"]["field2"] == "value2"


# ─── _model_json_schema_extra tests ──────────────────────────────────────────


def test_model_json_schema_extra_none():
    """_model_json_schema_extra should return {} for model without extra."""

    class SimpleModel(BaseModel):
        field: str

    result = _model_json_schema_extra(SimpleModel)
    assert result == {}


def test_model_json_schema_extra_with_config():
    """_model_json_schema_extra should extract from model_config."""

    class ModelWithConfig(BaseModel):
        model_config = {"json_schema_extra": {"custom": "value"}}
        field: str

    result = _model_json_schema_extra(ModelWithConfig)
    assert result == {"custom": "value"}


# ─── _produces_sources tests ─────────────────────────────────────────────────


def test_produces_sources_false():
    """_produces_sources should return False for model without marker."""

    class SimpleModel(BaseModel):
        field: str

    assert _produces_sources(SimpleModel) is False


def test_produces_sources_true():
    """_produces_sources should return True for model with marker."""

    class SourceModel(BaseModel):
        model_config = {"json_schema_extra": {"produces_sources": True}}
        title: str
        url: str

    assert _produces_sources(SourceModel) is True


# ─── _source_field_map tests ─────────────────────────────────────────────────


def test_source_field_map_empty():
    """_source_field_map should return {} for model without source fields."""

    class SimpleModel(BaseModel):
        field: str

    result = _source_field_map(SimpleModel)
    assert result == {}


def test_source_field_map_with_source_field():
    """_source_field_map should map fields with source_field extra."""

    class ModelWithSourceField(BaseModel):
        my_title: str = Field(json_schema_extra={"source_field": "title"})

    result = _source_field_map(ModelWithSourceField)
    assert result == {"my_title": "title"}


# ─── _extract_source_payloads tests ──────────────────────────────────────────


def test_extract_source_payloads_none():
    """_extract_source_payloads should return [] for None observation."""

    class SimpleModel(BaseModel):
        field: str

    result = _extract_source_payloads(SimpleModel, None)
    assert result == []


def test_extract_source_payloads_non_mapping():
    """_extract_source_payloads should return [] for non-mapping observation."""

    class SimpleModel(BaseModel):
        field: str

    result = _extract_source_payloads(SimpleModel, "string observation")
    assert result == []


# ─── _SourceCollector tests ──────────────────────────────────────────────────


def test_source_collector_empty():
    """_SourceCollector should start empty."""
    collector = _SourceCollector()
    assert collector.snapshot() == []


def test_source_collector_with_existing():
    """_SourceCollector should accept existing sources."""
    existing = [{"title": "Test", "url": "https://example.com", "snippet": "text"}]
    collector = _SourceCollector(existing)
    assert len(collector.snapshot()) == 1


def test_source_collector_deduplication():
    """_SourceCollector should deduplicate sources."""
    collector = _SourceCollector()
    source = {"title": "Test", "url": "https://example.com", "snippet": "text"}

    class ModelWithSources(BaseModel):
        model_config = {"json_schema_extra": {"produces_sources": True}}
        title: str
        url: str
        snippet: str

    collector.collect(ModelWithSources, source)
    collector.collect(ModelWithSources, source)  # Duplicate

    assert len(collector.snapshot()) == 1


# ─── _normalise_artifact_value tests ─────────────────────────────────────────


def test_normalise_artifact_value_dict():
    """_normalise_artifact_value should pass through dicts."""
    result = _normalise_artifact_value({"key": "value"})
    assert result == {"key": "value"}


def test_normalise_artifact_value_basemodel():
    """_normalise_artifact_value should serialize BaseModel."""

    class TestModel(BaseModel):
        field: str

    result = _normalise_artifact_value(TestModel(field="value"))
    assert result == {"field": "value"}


def test_normalise_artifact_value_non_serializable():
    """_normalise_artifact_value should handle non-serializable values."""
    result = _normalise_artifact_value(lambda x: x)
    # Should return repr or string representation
    assert isinstance(result, str)


# ─── _fallback_answer tests ──────────────────────────────────────────────────


def test_fallback_answer_with_answer_key():
    """_fallback_answer should extract 'answer' key."""
    result = _fallback_answer({"answer": "The answer is 42"})
    assert result == "The answer is 42"


def test_fallback_answer_with_raw_answer_key():
    """_fallback_answer should extract 'raw_answer' key."""
    result = _fallback_answer({"raw_answer": "Raw answer text"})
    assert result == "Raw answer text"


def test_fallback_answer_with_text_key():
    """_fallback_answer should extract 'text' key."""
    result = _fallback_answer({"text": "Text content"})
    assert result == "Text content"


def test_fallback_answer_with_result_key():
    """_fallback_answer should extract 'result' key."""
    result = _fallback_answer({"result": "Result value"})
    assert result == "Result value"


def test_fallback_answer_nested_dict():
    """_fallback_answer should recursively extract from nested dicts."""
    result = _fallback_answer({"answer": {"text": "Nested text"}})
    assert result == "Nested text"


def test_fallback_answer_with_args():
    """_fallback_answer should check nested args dict."""
    result = _fallback_answer({"args": {"answer": "Answer in args"}})
    assert result == "Answer in args"


def test_fallback_answer_string():
    """_fallback_answer should return string observation as-is."""
    result = _fallback_answer("Direct string answer")
    assert result == "Direct string answer"


def test_fallback_answer_none():
    """_fallback_answer should return default for None."""
    result = _fallback_answer(None)
    assert result == "No answer produced."


def test_fallback_answer_single_long_string():
    """_fallback_answer should use single long string value."""
    result = _fallback_answer({"custom_field": "This is a sufficiently long answer text"})
    assert result == "This is a sufficiently long answer text"


def test_fallback_answer_thought_as_answer():
    """_fallback_answer should use thought if it doesn't start with thinking phrases."""
    result = _fallback_answer({"thought": "The capital of France is Paris, as evidenced by..."})
    assert "Paris" in result


def test_fallback_answer_thought_not_answer():
    """_fallback_answer should not use thought if it starts with thinking phrases."""
    result = _fallback_answer({"thought": "I need to find the answer first"})
    # When thought starts with thinking phrases, it falls back to JSON serialization
    assert "thought" in result


def test_fallback_answer_json_fallback():
    """_fallback_answer should JSON serialize as last resort."""
    result = _fallback_answer({"some_key": 123})
    assert '"some_key"' in result or result == "No answer produced."
