"""Tests for helper functions in penguiflow/planner/react.py."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

import pytest
from pydantic import BaseModel, Field
from typing_extensions import override

from penguiflow.catalog import NodeSpec
from penguiflow.node import Node
from penguiflow.planner.artifact_handling import (
    _ArtifactCollector,
    _extract_source_payloads,
    _model_json_schema_extra,
    _normalise_artifact_value,
    _produces_sources,
    _source_field_map,
    _SourceCollector,
)
from penguiflow.planner.models import JSONLLMClient
from penguiflow.planner.payload_builders import _fallback_answer
from penguiflow.planner.streaming import (
    _StreamingArgsExtractor,
    _StreamingThoughtExtractor,
)
from penguiflow.planner.validation_repair import (
    _coerce_tool_context,
    _default_for_annotation,
    _salvage_action_payload,
    _validate_llm_context,
)

if TYPE_CHECKING:
    from penguiflow.planner import ReactPlanner

# ─── _validate_llm_context tests ─────────────────────────────────────────────


def test_validate_llm_context_none() -> None:
    """_validate_llm_context should return None for None input."""
    result = _validate_llm_context(None)
    assert result is None


def test_validate_llm_context_valid() -> None:
    """_validate_llm_context should return dict for valid mapping."""
    result = _validate_llm_context({"key": "value", "num": 42})
    assert result == {"key": "value", "num": 42}


def test_validate_llm_context_not_mapping() -> None:
    """_validate_llm_context should raise for non-mapping."""
    with pytest.raises(TypeError, match="must be a mapping"):
        _validate_llm_context("not a mapping")


def test_validate_llm_context_not_serializable() -> None:
    """_validate_llm_context should raise for non-JSON-serializable."""
    with pytest.raises(TypeError, match="must be JSON-serializable"):
        _validate_llm_context({"func": lambda x: x})


# ─── _coerce_tool_context tests ──────────────────────────────────────────────


def test_coerce_tool_context_none() -> None:
    """_coerce_tool_context should return empty dict for None."""
    result = _coerce_tool_context(None)
    assert result == {}


def test_coerce_tool_context_valid() -> None:
    """_coerce_tool_context should return dict for valid mapping."""
    result = _coerce_tool_context({"key": "value"})
    assert result == {"key": "value"}


def test_coerce_tool_context_not_mapping() -> None:
    """_coerce_tool_context should raise for non-mapping."""
    with pytest.raises(TypeError, match="must be a mapping"):
        _coerce_tool_context("not a mapping")


# ─── _salvage_action_payload tests ───────────────────────────────────────────


def test_salvage_action_payload_valid_json() -> None:
    """_salvage_action_payload should parse valid JSON."""
    raw = (
        '{"thought": "thinking", "next_node": "tool_x", "args": {"q": "test"}}'
    )
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "thinking"
    assert result.next_node == "tool_x"


def test_salvage_action_payload_minimal() -> None:
    """_salvage_action_payload should fill defaults for minimal JSON."""
    raw = '{"thought": "thinking"}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "thinking"
    assert result.next_node == "final_response"


def test_salvage_action_payload_no_thought() -> None:
    """_salvage_action_payload should provide default thought."""
    raw = '{"next_node": "some_tool"}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "planning next step"


def test_salvage_action_payload_nested_action() -> None:
    """_salvage_action_payload should unwrap nested action dict."""
    raw = '{"action": {"thought": "nested thought", "next_node": "tool_y"}}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "nested thought"
    assert result.next_node == "tool_y"


def test_salvage_action_payload_with_plan() -> None:
    """_salvage_action_payload should normalize plan entries."""
    raw = (
        '{"thought": "parallel", "plan": [{"node": "tool_a"}, {"node": '
        '"tool_b", "args": {"x": 1}}]}'
    )
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.next_node == "parallel"
    assert len(result.args["steps"]) == 2


def test_salvage_action_payload_invalid_plan_entries() -> None:
    """_salvage_action_payload should skip invalid plan entries."""
    raw = (
        '{"thought": "parallel", "plan": ["invalid", {"no_node": true}, '
        '{"node": "valid"}]}'
    )
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.next_node == "parallel"
    assert len(result.args["steps"]) == 1


def test_salvage_action_payload_python_literal() -> None:
    """_salvage_action_payload should handle Python literal syntax."""
    raw = "{'thought': 'python style', 'next_node': None}"
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.thought == "python style"


def test_salvage_action_payload_invalid() -> None:
    """_salvage_action_payload should return None for invalid input."""
    result = _salvage_action_payload("not valid json or python")
    assert result is None


def test_salvage_action_payload_fills_args_when_missing() -> None:
    """_salvage_action_payload fills args={} when args are missing."""
    raw = '{"thought": "test", "next_node": "some_tool"}'
    result = _salvage_action_payload(raw)
    assert result is not None
    assert result.args == {}


# ─── _default_for_annotation tests ───────────────────────────────────────────


def test_default_for_annotation_literal() -> None:
    """_default_for_annotation should return first value for Literal."""
    result = _default_for_annotation(Literal["a", "b", "c"])
    assert result == "a"


def test_default_for_annotation_str() -> None:
    """_default_for_annotation should return '<auto>' for str."""
    result = _default_for_annotation(str)
    assert result == "<auto>"


def test_default_for_annotation_bool() -> None:
    """_default_for_annotation should return False for bool."""
    result = _default_for_annotation(bool)
    assert result is False


def test_default_for_annotation_int() -> None:
    """_default_for_annotation should return 0 for int."""
    result = _default_for_annotation(int)
    assert result == 0


def test_default_for_annotation_float() -> None:
    """_default_for_annotation should return 0.0 for float."""
    result = _default_for_annotation(float)
    assert result == 0.0


def test_default_for_annotation_list() -> None:
    """_default_for_annotation should return [] for list."""
    result = _default_for_annotation(list[str])
    assert result == []


def test_default_for_annotation_dict() -> None:
    """_default_for_annotation should return {} for dict."""
    result = _default_for_annotation(dict[str, int])
    assert result == {}


def test_default_for_annotation_sequence() -> None:
    """_default_for_annotation should return [] for Sequence."""
    result = _default_for_annotation(Sequence[str])
    assert result == []


def test_default_for_annotation_basemodel() -> None:
    """_default_for_annotation should return {} for BaseModel subclass."""

    class TestModel(BaseModel):
        field: str

    result = _default_for_annotation(TestModel)
    assert result == {}


def test_default_for_annotation_optional() -> None:
    """_default_for_annotation should handle Optional (Union with None)."""
    result = _default_for_annotation(str | None)
    # Should return "<auto>" for the str part (AUTO_STR_SENTINEL)
    assert result == "<auto>"


def test_default_for_annotation_unknown() -> None:
    """_default_for_annotation should return '<auto>' for unknown types."""

    class CustomClass:
        pass

    result = _default_for_annotation(CustomClass)
    assert result == "<auto>"


# ─── _StreamingArgsExtractor tests ───────────────────────────────────────────


def test_streaming_args_extractor_non_finish() -> None:
    """_StreamingArgsExtractor should not extract for non-finish actions."""
    extractor = _StreamingArgsExtractor()
    extractor.feed('{"thought": "thinking", "next_node": "tool_x"')
    assert not extractor.is_finish_action


def test_streaming_args_extractor_finish_detection() -> None:
    """_StreamingArgsExtractor should detect finish action."""
    extractor = _StreamingArgsExtractor()
    extractor.feed('{"thought": "done", "next_node": null')
    assert extractor.is_finish_action


def test_streaming_args_extractor_extracts_answer() -> None:
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


def test_streaming_args_extractor_args_before_next_node() -> None:
    """_StreamingArgsExtractor should stream when args precede next_node."""
    extractor = _StreamingArgsExtractor()
    chunks = []
    chunks.extend(
        extractor.feed('{"thought": "done", "args": {"raw_answer": "Hi')
    )
    chunks.extend(extractor.feed(' there"}, "next_node": null}'))

    text = "".join(chunks)
    assert "Hi" in text or extractor.emitted_count > 0
    assert extractor.is_finish_action


def test_streaming_args_extractor_handles_escapes() -> None:
    """_StreamingArgsExtractor should handle escape sequences."""
    extractor = _StreamingArgsExtractor()
    # Start with finish detection
    extractor.feed(
        '{"thought": "done", "next_node": null, "args": {"answer": '
        '"line1\\nline2"}}'
    )
    # The extractor should handle \n escape


# ─── _StreamingThoughtExtractor tests ────────────────────────────────────────


def test_streaming_thought_extractor_basic() -> None:
    """_StreamingThoughtExtractor should extract thought content."""
    extractor = _StreamingThoughtExtractor()
    chunks = []
    chunks.extend(extractor.feed('{"thought": "Planning'))
    chunks.extend(extractor.feed(' the next step"'))

    text = "".join(chunks)
    assert "Planning" in text or extractor.emitted_count > 0


def test_streaming_thought_extractor_with_escapes() -> None:
    """_StreamingThoughtExtractor should handle escapes."""
    extractor = _StreamingThoughtExtractor()
    extractor.feed('{"thought": "Step 1:\\nStep 2"')
    # Should handle \n escape without error


# ─── _ArtifactCollector tests ────────────────────────────────────────────────


def test_artifact_collector_empty() -> None:
    """_ArtifactCollector should start empty."""
    collector = _ArtifactCollector()
    assert collector.snapshot() == {}


def test_artifact_collector_with_existing() -> None:
    """_ArtifactCollector should accept existing artifacts."""
    existing = {"tool1": {"data": "value"}}
    collector = _ArtifactCollector(existing)
    assert collector.snapshot() == {"tool1": {"data": "value"}}


def test_artifact_collector_collect() -> None:
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


def test_artifact_collector_ignores_outputs_without_artifact_fields() -> None:
    """_ArtifactCollector should collect nothing without artifact fields."""
    collector = _ArtifactCollector()

    class OutputModel(BaseModel):
        result: str

    collector.collect("test_node", OutputModel, {"result": "plain"})
    assert collector.snapshot() == {}


def test_artifact_collector_merge() -> None:
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


def test_model_json_schema_extra_none() -> None:
    """_model_json_schema_extra should return {} for model without extra."""

    class SimpleModel(BaseModel):
        field: str

    result = _model_json_schema_extra(SimpleModel)
    assert result == {}


def test_model_json_schema_extra_with_config() -> None:
    """_model_json_schema_extra should extract from model_config."""

    class ModelWithConfig(BaseModel):
        model_config = {"json_schema_extra": {"custom": "value"}}
        field: str

    result = _model_json_schema_extra(ModelWithConfig)
    assert result == {"custom": "value"}


# ─── _produces_sources tests ─────────────────────────────────────────────────


def test_produces_sources_false() -> None:
    """_produces_sources should return False for model without marker."""

    class SimpleModel(BaseModel):
        field: str

    assert _produces_sources(SimpleModel) is False


def test_produces_sources_true() -> None:
    """_produces_sources should return True for model with marker."""

    class SourceModel(BaseModel):
        model_config = {"json_schema_extra": {"produces_sources": True}}
        title: str
        url: str

    assert _produces_sources(SourceModel) is True


# ─── _source_field_map tests ─────────────────────────────────────────────────


def test_source_field_map_empty() -> None:
    """_source_field_map should return {} for model without source fields."""

    class SimpleModel(BaseModel):
        field: str

    result = _source_field_map(SimpleModel)
    assert result == {}


def test_source_field_map_with_source_field() -> None:
    """_source_field_map should map fields with source_field extra."""

    class ModelWithSourceField(BaseModel):
        my_title: str = Field(json_schema_extra={"source_field": "title"})

    result = _source_field_map(ModelWithSourceField)
    assert result == {"my_title": "title"}


# ─── _extract_source_payloads tests ──────────────────────────────────────────


def test_extract_source_payloads_none() -> None:
    """_extract_source_payloads should return [] for None observation."""

    class SimpleModel(BaseModel):
        field: str

    result = _extract_source_payloads(SimpleModel, None)
    assert result == []


def test_extract_source_payloads_non_mapping() -> None:
    """_extract_source_payloads returns [] for non-mappings."""

    class SimpleModel(BaseModel):
        field: str

    result = _extract_source_payloads(SimpleModel, "string observation")
    assert result == []


# ─── _SourceCollector tests ──────────────────────────────────────────────────


def test_source_collector_empty() -> None:
    """_SourceCollector should start empty."""
    collector = _SourceCollector()
    assert collector.snapshot() == []


def test_source_collector_with_existing() -> None:
    """_SourceCollector should accept existing sources."""
    existing = [
        {"title": "Test", "url": "https://example.com", "snippet": "text"}
    ]
    collector = _SourceCollector(existing)
    assert len(collector.snapshot()) == 1


def test_source_collector_deduplication() -> None:
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


def test_normalise_artifact_value_dict() -> None:
    """_normalise_artifact_value should pass through dicts."""
    result = _normalise_artifact_value({"key": "value"})
    assert result == {"key": "value"}


def test_normalise_artifact_value_basemodel() -> None:
    """_normalise_artifact_value should serialize BaseModel."""

    class TestModel(BaseModel):
        field: str

    result = _normalise_artifact_value(TestModel(field="value"))
    assert result == {"field": "value"}


def test_normalise_artifact_value_non_serializable() -> None:
    """_normalise_artifact_value should handle non-serializable values."""
    result = _normalise_artifact_value(lambda x: x)
    # Should return repr or string representation
    assert isinstance(result, str)


# ─── _fallback_answer tests ──────────────────────────────────────────────────


def test_fallback_answer_with_answer_key() -> None:
    """_fallback_answer should extract 'answer' key."""
    result = _fallback_answer({"answer": "The answer is 42"})
    assert result == "The answer is 42"


def test_fallback_answer_with_raw_answer_key() -> None:
    """_fallback_answer should extract 'raw_answer' key."""
    result = _fallback_answer({"raw_answer": "Raw answer text"})
    assert result == "Raw answer text"


def test_fallback_answer_with_text_key() -> None:
    """_fallback_answer should extract 'text' key."""
    result = _fallback_answer({"text": "Text content"})
    assert result == "Text content"


def test_fallback_answer_with_result_key() -> None:
    """_fallback_answer should extract 'result' key."""
    result = _fallback_answer({"result": "Result value"})
    assert result == "Result value"


def test_fallback_answer_nested_dict() -> None:
    """_fallback_answer should recursively extract from nested dicts."""
    result = _fallback_answer({"answer": {"text": "Nested text"}})
    assert result == "Nested text"


def test_fallback_answer_with_args() -> None:
    """_fallback_answer should check nested args dict."""
    result = _fallback_answer({"args": {"answer": "Answer in args"}})
    assert result == "Answer in args"


def test_fallback_answer_string() -> None:
    """_fallback_answer should return string observation as-is."""
    result = _fallback_answer("Direct string answer")
    assert result == "Direct string answer"


def test_fallback_answer_none() -> None:
    """_fallback_answer should return default for None."""
    result = _fallback_answer(None)
    assert result == "No answer produced."


def test_fallback_answer_single_long_string() -> None:
    """_fallback_answer should use single long string value."""
    result = _fallback_answer(
        {"custom_field": "This is a sufficiently long answer text"}
    )
    assert result == "This is a sufficiently long answer text"


def test_fallback_answer_thought_as_answer() -> None:
    """_fallback_answer uses a thought without thinking phrases."""
    result = _fallback_answer(
        {"thought": "The capital of France is Paris, as evidenced by..."}
    )
    assert "Paris" in result


def test_fallback_answer_thought_not_answer() -> None:
    """_fallback_answer skips a thought with thinking phrases."""
    result = _fallback_answer({"thought": "I need to find the answer first"})
    # When thought starts with thinking phrases, it falls back to JSON
    # serialization
    assert "thought" in result


def test_fallback_answer_json_fallback() -> None:
    """_fallback_answer should JSON serialize as last resort."""
    result = _fallback_answer({"some_key": 123})
    assert '"some_key"' in result or result == "No answer produced."


# ─── Arg-fill and Finish-repair parsing tests ────────────────────────────────


class MockJSONLLMClient(JSONLLMClient):
    """Minimal mock client for testing ReactPlanner methods."""

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        return '{"thought": "test", "next_node": null}'


class _NoopOut(BaseModel):
    """Output of the no-op tool behind ``_spec_for``."""


async def _noop(_args: BaseModel, _ctx: object) -> _NoopOut:
    """No-op tool whose spec only matters for its argument model."""
    return _NoopOut()


def _spec_for(args_model: type[BaseModel]) -> NodeSpec:
    """Build a tool spec whose arguments use ``args_model``."""
    return NodeSpec(
        node=Node(_noop, name="noop"),
        name="noop",
        desc="No-op tool.",
        args_model=args_model,
        out_model=_NoopOut,
    )


@pytest.fixture
def minimal_planner() -> ReactPlanner:
    """Create a minimal ReactPlanner for testing helper methods."""
    from penguiflow.catalog import build_catalog
    from penguiflow.planner import ReactPlanner
    from penguiflow.registry import ModelRegistry

    catalog = build_catalog([], ModelRegistry())
    return ReactPlanner(
        llm_client=MockJSONLLMClient(),
        catalog=catalog,
        max_iters=1,
    )


class TestParseArgFillResponse:
    """Tests for ReactPlanner._parse_arg_fill_response."""

    def test_valid_json(self, minimal_planner: ReactPlanner) -> None:
        """Should parse valid JSON response."""
        raw = '{"query": "test query", "limit": 10}'
        result = minimal_planner.parse_arg_fill_response(
            raw, ["query", "limit"]
        )
        assert result == {"query": "test query", "limit": 10}

    def test_json_with_markdown_fences(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should strip markdown code fences."""
        raw = '```json\n{"query": "test"}\n```'
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result == {"query": "test"}

    def test_json_partial_fields(self, minimal_planner: ReactPlanner) -> None:
        """Should extract only expected fields from JSON."""
        raw = '{"query": "test", "extra_field": "ignored"}'
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result == {"query": "test"}

    def test_placeholder_auto_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject <auto> placeholder."""
        raw = '{"query": "<auto>"}'
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result is None

    def test_placeholder_unknown_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject 'unknown' placeholder."""
        raw = '{"query": "unknown"}'
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result is None

    def test_placeholder_na_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject 'n/a' placeholder."""
        raw = '{"query": "n/a"}'
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result is None

    def test_placeholder_empty_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject empty string placeholder."""
        raw = '{"query": ""}'
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result is None

    def test_tagged_format(self, minimal_planner: ReactPlanner) -> None:
        """Should parse tagged format <field>value</field>."""
        raw = "<query>search term</query><limit>5</limit>"
        result = minimal_planner.parse_arg_fill_response(
            raw, ["query", "limit"]
        )
        assert result == {"query": "search term", "limit": "5"}

    def test_tagged_format_with_whitespace(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should handle whitespace in tagged values."""
        raw = "<query>  search term  </query>"
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result == {"query": "search term"}

    def test_tagged_format_placeholder_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject placeholders in tagged format."""
        raw = "<query><auto></query>"
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result is None

    def test_invalid_json_no_tags(self, minimal_planner: ReactPlanner) -> None:
        """Should return None for invalid input."""
        raw = "This is not valid JSON or tags"
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result is None

    def test_no_expected_fields_found(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should return None when no expected fields found."""
        raw = '{"other_field": "value"}'
        result = minimal_planner.parse_arg_fill_response(raw, ["query"])
        assert result is None


class TestParseFinishRepairResponse:
    """Tests for ReactPlanner._parse_finish_repair_response."""

    def test_json_with_raw_answer(self, minimal_planner: ReactPlanner) -> None:
        """Should extract raw_answer from JSON."""
        raw = '{"raw_answer": "The answer is 42"}'
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "The answer is 42"

    def test_json_with_answer(self, minimal_planner: ReactPlanner) -> None:
        """Should extract answer key from JSON."""
        raw = '{"answer": "The answer is 42"}'
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "The answer is 42"

    def test_json_with_text(self, minimal_planner: ReactPlanner) -> None:
        """Should extract text key from JSON."""
        raw = '{"text": "The answer is 42"}'
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "The answer is 42"

    def test_json_with_response(self, minimal_planner: ReactPlanner) -> None:
        """Should extract response key from JSON."""
        raw = '{"response": "The answer is 42"}'
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "The answer is 42"

    def test_json_with_content(self, minimal_planner: ReactPlanner) -> None:
        """Should extract content key from JSON."""
        raw = '{"content": "The answer is 42"}'
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "The answer is 42"

    def test_json_with_markdown_fences(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should strip markdown code fences."""
        raw = '```json\n{"raw_answer": "Test"}\n```'
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "Test"

    def test_json_placeholder_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject placeholder values in JSON."""
        raw = '{"raw_answer": "<auto>"}'
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result is None

    def test_plain_text(self, minimal_planner: ReactPlanner) -> None:
        """Should accept plain text response."""
        raw = "The answer is simply 42"
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "The answer is simply 42"

    def test_plain_text_with_prefix(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should strip common prefixes from plain text."""
        raw = "raw_answer: The answer is 42"
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "The answer is 42"

    def test_plain_text_placeholder_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject placeholder plain text."""
        raw = "<auto>"
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result is None

    def test_empty_answer_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject empty answer in JSON."""
        raw = '{"raw_answer": ""}'
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result is None

    def test_whitespace_only_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject whitespace-only answer."""
        raw = '{"raw_answer": "   "}'
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result is None

    def test_nested_args_raw_answer(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Extract raw_answer from nested args (full action schema)."""
        raw = (
            '{"thought": "Greeting user", "next_node": null, "args": '
            '{"raw_answer": "Hello there!"}}'
        )
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "Hello there!"

    def test_nested_args_answer(self, minimal_planner: ReactPlanner) -> None:
        """Should extract answer from nested args dict."""
        raw = (
            '{"thought": "Responding", "next_node": null, "args": '
            '{"answer": "Test response"}}'
        )
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result == "Test response"

    def test_nested_args_placeholder_rejected(
        self, minimal_planner: ReactPlanner
    ) -> None:
        """Should reject placeholder values in nested args."""
        raw = (
            '{"thought": "Test", "next_node": null, "args": '
            '{"raw_answer": "<auto>"}}'
        )
        result = minimal_planner.parse_finish_repair_response(raw)
        assert result is None


class TestRenderFinishGuidance:
    """Tests for render_finish_guidance tiered prompts."""

    def test_no_guidance_for_zero(self) -> None:
        """Should return None when count is 0."""
        from penguiflow.planner.prompts import render_finish_guidance

        assert render_finish_guidance(0) is None

    def test_no_guidance_for_negative(self) -> None:
        """Should return None for negative counts."""
        from penguiflow.planner.prompts import render_finish_guidance

        assert render_finish_guidance(-1) is None

    def test_gentle_reminder_for_one(self) -> None:
        """Should return gentle reminder for count=1."""
        from penguiflow.planner.prompts import render_finish_guidance

        result = render_finish_guidance(1)
        assert result is not None
        assert "REMINDER" in result
        assert "args.answer" in result

    def test_firm_warning_for_two(self) -> None:
        """Should return firm warning for count=2."""
        from penguiflow.planner.prompts import render_finish_guidance

        result = render_finish_guidance(2)
        assert result is not None
        assert "IMPORTANT" in result
        assert "args.answer" in result

    def test_critical_for_three_plus(self) -> None:
        """Should return critical warning for count>=3."""
        from penguiflow.planner.prompts import render_finish_guidance

        result = render_finish_guidance(3)
        assert result is not None
        assert "CRITICAL" in result
        # Same message for higher counts
        assert render_finish_guidance(5) == render_finish_guidance(3)


class TestRenderArgFillGuidance:
    """Tests for render_arg_fill_guidance tiered guidance."""

    def test_no_guidance_for_zero(self) -> None:
        """Should return None when count is 0."""
        from penguiflow.planner.prompts import render_arg_fill_guidance

        assert render_arg_fill_guidance(0) is None

    def test_no_guidance_for_negative(self) -> None:
        """Should return None for negative counts."""
        from penguiflow.planner.prompts import render_arg_fill_guidance

        assert render_arg_fill_guidance(-1) is None

    def test_gentle_reminder_for_one(self) -> None:
        """Should return gentle reminder for count=1."""
        from penguiflow.planner.prompts import render_arg_fill_guidance

        result = render_arg_fill_guidance(1)
        assert result is not None
        assert "REMINDER" in result
        assert "<auto>" in result

    def test_firm_warning_for_two(self) -> None:
        """Should return firm warning for count=2."""
        from penguiflow.planner.prompts import render_arg_fill_guidance

        result = render_arg_fill_guidance(2)
        assert result is not None
        assert "IMPORTANT" in result
        assert "valid options" in result.lower()

    def test_critical_for_three_plus(self) -> None:
        """Should return critical warning for count>=3."""
        from penguiflow.planner.prompts import render_arg_fill_guidance

        result = render_arg_fill_guidance(3)
        assert result is not None
        assert "CRITICAL" in result
        # Same message for higher counts
        assert render_arg_fill_guidance(5) == render_arg_fill_guidance(3)


class TestRenderMultiActionGuidance:
    """Tests for render_multi_action_guidance tiered guidance."""

    def test_no_guidance_for_zero(self) -> None:
        """No guidance for zero."""
        from penguiflow.planner.prompts import render_multi_action_guidance

        assert render_multi_action_guidance(0) is None

    def test_no_guidance_for_negative(self) -> None:
        """No guidance for negative."""
        from penguiflow.planner.prompts import render_multi_action_guidance

        assert render_multi_action_guidance(-1) is None

    def test_gentle_reminder_for_one(self) -> None:
        """Gentle reminder for one."""
        from penguiflow.planner.prompts import render_multi_action_guidance

        result = render_multi_action_guidance(1)
        assert result is not None
        assert "REMINDER" in result
        assert "ONE JSON object" in result

    def test_firm_warning_for_two(self) -> None:
        """Firm warning for two."""
        from penguiflow.planner.prompts import render_multi_action_guidance

        result = render_multi_action_guidance(2)
        assert result is not None
        assert "IMPORTANT" in result
        assert "parallel" in result

    def test_critical_for_three_plus(self) -> None:
        """Critical for three plus."""
        from penguiflow.planner.prompts import render_multi_action_guidance

        result = render_multi_action_guidance(3)
        assert result is not None
        assert "CRITICAL" in result
        assert render_multi_action_guidance(5) == render_multi_action_guidance(
            3
        )


class TestRenderComponentGuidance:
    """Tests for render_render_component_guidance tiered guidance."""

    def test_wrapper_tools_are_mentioned_in_guidance(self) -> None:
        """Wrapper tools are mentioned in guidance."""
        from penguiflow.planner.prompts import render_render_component_guidance

        reminder = render_render_component_guidance(1)
        warning = render_render_component_guidance(2)
        critical = render_render_component_guidance(3)

        assert reminder is not None and "build/render tools" in reminder
        assert (
            warning is not None
            and "build_*" in warning
            and 'next_node="parallel"' in warning
        )
        assert (
            critical is not None
            and "artifact_ref" in critical
            and "one giant nested payload" in critical
        )


class TestExtractFieldDescriptions:
    """Tests for _extract_field_descriptions enhanced extraction."""

    def test_extracts_basic_description(self) -> None:
        """Should extract field description from schema."""
        from penguiflow.planner.validation_repair import (
            _extract_field_descriptions,
        )

        class ArgsWithDesc(BaseModel):
            query: str = Field(description="The search query")

        result = _extract_field_descriptions(_spec_for(ArgsWithDesc))
        assert "query" in result
        assert "The search query" in result["query"]

    def test_extracts_enum_values(self) -> None:
        """Should extract enum values from schema."""
        from penguiflow.planner.validation_repair import (
            _extract_field_descriptions,
        )

        class Color(str):
            pass

        class ArgsWithEnum(BaseModel):
            color: Literal["red", "green", "blue"] = Field(
                description="The color"
            )

        result = _extract_field_descriptions(_spec_for(ArgsWithEnum))
        assert "color" in result
        # Should include Valid options
        assert "Valid options:" in result["color"]
        assert "red" in result["color"]

    def test_extracts_examples(self) -> None:
        """Should extract examples from schema."""
        from penguiflow.planner.validation_repair import (
            _extract_field_descriptions,
        )

        class ArgsWithExamples(BaseModel):
            name: str = Field(
                description="User name", examples=["Alice", "Bob"]
            )

        result = _extract_field_descriptions(_spec_for(ArgsWithExamples))
        assert "name" in result
        assert "Examples:" in result["name"]

    def test_handles_missing_info_gracefully(self) -> None:
        """Should handle fields without descriptions."""
        from penguiflow.planner.validation_repair import (
            _extract_field_descriptions,
        )

        class ArgsNoDesc(BaseModel):
            query: str

        result = _extract_field_descriptions(_spec_for(ArgsNoDesc))
        # Should not have entry for field without any hints
        assert result.get("query") is None or result.get("query") == ""


class TestRenderArgFillPrompt:
    """Tests for render_arg_fill_prompt with enhanced hints."""

    def test_uses_enum_value_in_example(self) -> None:
        """Example JSON uses the first enum value, not a placeholder."""
        from penguiflow.planner.prompts import render_arg_fill_prompt

        result = render_arg_fill_prompt(
            tool_name="test_tool",
            missing_fields=["color"],
            field_descriptions={
                "color": "The color | Valid options: ['red', 'green', 'blue']"
            },
        )

        # Should use 'red' (first option) in the example, not "your value here"
        assert '"color": "red"' in result

    def test_adds_constraint_note_for_enum_fields(self) -> None:
        """Should add note about valid options when fields have constraints."""
        from penguiflow.planner.prompts import render_arg_fill_prompt

        result = render_arg_fill_prompt(
            tool_name="test_tool",
            missing_fields=["component"],
            field_descriptions={
                "component": "Valid options: ['chart', 'table']"
            },
        )

        assert "MUST use one of the listed values" in result

    def test_no_constraint_note_for_regular_fields(self) -> None:
        """Should not add constraint note when no enum fields."""
        from penguiflow.planner.prompts import render_arg_fill_prompt

        result = render_arg_fill_prompt(
            tool_name="test_tool",
            missing_fields=["query"],
            field_descriptions={"query": "The search query"},
        )

        assert "MUST use one of the listed values" not in result


class TestIsArgFillEligible:
    """Tests for ReactPlanner._is_arg_fill_eligible."""

    def test_disabled_returns_false(self) -> None:
        """Should return False when arg_fill_enabled is False."""
        from penguiflow.catalog import build_catalog, tool
        from penguiflow.node import Node
        from penguiflow.planner import ReactPlanner
        from penguiflow.planner.react import Trajectory
        from penguiflow.registry import ModelRegistry

        class SimpleArgs(BaseModel):
            field: str = ""

        class SimpleOut(BaseModel):
            result: str = ""

        @tool(desc="Simple tool")
        async def simple(_args: SimpleArgs, _ctx: object) -> dict[str, object]:
            return {}

        registry = ModelRegistry()
        registry.register("simple", SimpleArgs, SimpleOut)
        catalog = build_catalog([Node(simple, name="simple")], registry)
        planner = ReactPlanner(
            llm_client=MockJSONLLMClient(),
            catalog=catalog,
            max_iters=1,
            arg_fill_enabled=False,
        )

        trajectory = Trajectory(query="test")
        spec = planner.spec_by_name["simple"]
        result = planner.is_arg_fill_eligible(spec, ["field"], trajectory)
        assert result is False

    def test_already_attempted_returns_false(self) -> None:
        """Should return False when arg_fill was already attempted."""
        from penguiflow.catalog import build_catalog, tool
        from penguiflow.node import Node
        from penguiflow.planner import ReactPlanner
        from penguiflow.planner.react import Trajectory
        from penguiflow.registry import ModelRegistry

        class SimpleArgs(BaseModel):
            query: str

        class SimpleOut(BaseModel):
            result: str = ""

        @tool(desc="Tool with string arg")
        async def string_tool(
            args: SimpleArgs, _ctx: object
        ) -> dict[str, object]:
            return {"result": args.query}

        registry = ModelRegistry()
        registry.register("string_tool", SimpleArgs, SimpleOut)
        catalog = build_catalog(
            [Node(string_tool, name="string_tool")], registry
        )
        planner = ReactPlanner(
            llm_client=MockJSONLLMClient(),
            catalog=catalog,
            max_iters=1,
            arg_fill_enabled=True,
        )

        trajectory = Trajectory(query="test")
        spec = planner.spec_by_name["string_tool"]
        # Per-tool flag now (not global)
        trajectory.metadata[f"arg_fill_attempted_{spec.name}"] = True

        result = planner.is_arg_fill_eligible(spec, ["query"], trajectory)
        assert result is False

    def test_simple_string_type_eligible(self) -> None:
        """Should be eligible for simple string fields."""
        from penguiflow.catalog import build_catalog, tool
        from penguiflow.node import Node
        from penguiflow.planner import ReactPlanner
        from penguiflow.planner.react import Trajectory
        from penguiflow.registry import ModelRegistry

        class SimpleArgs(BaseModel):
            query: str

        class SimpleOut(BaseModel):
            result: str = ""

        @tool(desc="Tool with string arg")
        async def string_tool(
            args: SimpleArgs, _ctx: object
        ) -> dict[str, object]:
            return {"result": args.query}

        registry = ModelRegistry()
        registry.register("string_tool", SimpleArgs, SimpleOut)
        catalog = build_catalog(
            [Node(string_tool, name="string_tool")], registry
        )
        planner = ReactPlanner(
            llm_client=MockJSONLLMClient(),
            catalog=catalog,
            max_iters=1,
            arg_fill_enabled=True,
        )

        trajectory = Trajectory(query="test")
        spec = planner.spec_by_name["string_tool"]
        result = planner.is_arg_fill_eligible(spec, ["query"], trajectory)
        assert result is True

    def test_simple_number_type_eligible(self) -> None:
        """Should be eligible for simple number fields."""
        from penguiflow.catalog import build_catalog, tool
        from penguiflow.node import Node
        from penguiflow.planner import ReactPlanner
        from penguiflow.planner.react import Trajectory
        from penguiflow.registry import ModelRegistry

        class NumberArgs(BaseModel):
            count: int
            rate: float

        class NumberOut(BaseModel):
            result: int = 0

        @tool(desc="Tool with number args")
        async def number_tool(
            args: NumberArgs, _ctx: object
        ) -> dict[str, object]:
            return {"result": args.count}

        registry = ModelRegistry()
        registry.register("number_tool", NumberArgs, NumberOut)
        catalog = build_catalog(
            [Node(number_tool, name="number_tool")], registry
        )
        planner = ReactPlanner(
            llm_client=MockJSONLLMClient(),
            catalog=catalog,
            max_iters=1,
            arg_fill_enabled=True,
        )

        trajectory = Trajectory(query="test")
        spec = planner.spec_by_name["number_tool"]
        result = planner.is_arg_fill_eligible(
            spec, ["count", "rate"], trajectory
        )
        assert result is True

    def test_complex_type_not_eligible(self) -> None:
        """Should not be eligible for complex types like list/dict."""
        from penguiflow.catalog import build_catalog, tool
        from penguiflow.node import Node
        from penguiflow.planner import ReactPlanner
        from penguiflow.planner.react import Trajectory
        from penguiflow.registry import ModelRegistry

        class ComplexArgs(BaseModel):
            items: list[str]

        class ComplexOut(BaseModel):
            result: int = 0

        @tool(desc="Tool with complex arg")
        async def complex_tool(
            args: ComplexArgs, _ctx: object
        ) -> dict[str, object]:
            return {"result": len(args.items)}

        registry = ModelRegistry()
        registry.register("complex_tool", ComplexArgs, ComplexOut)
        catalog = build_catalog(
            [Node(complex_tool, name="complex_tool")], registry
        )
        planner = ReactPlanner(
            llm_client=MockJSONLLMClient(),
            catalog=catalog,
            max_iters=1,
            arg_fill_enabled=True,
        )

        trajectory = Trajectory(query="test")
        spec = planner.spec_by_name["complex_tool"]
        result = planner.is_arg_fill_eligible(spec, ["items"], trajectory)
        assert result is False

    def test_render_report_sections_is_eligible(self) -> None:
        """Rich-output wrapper payload fields are eligible for arg-fill."""
        from penguiflow.catalog import build_catalog
        from penguiflow.planner import ReactPlanner
        from penguiflow.planner.react import Trajectory
        from penguiflow.registry import ModelRegistry
        from penguiflow.rich_output.runtime import (
            RichOutputConfig,
            attach_rich_output_nodes,
            configure_rich_output,
            reset_runtime,
        )

        reset_runtime()
        config = RichOutputConfig(
            enabled=True, allowlist=["report", "markdown"]
        )
        configure_rich_output(config)
        registry = ModelRegistry()
        catalog = build_catalog(
            list(attach_rich_output_nodes(registry, config=config)), registry
        )
        planner = ReactPlanner(
            llm_client=MockJSONLLMClient(),
            catalog=catalog,
            max_iters=1,
            arg_fill_enabled=True,
        )

        trajectory = Trajectory(query="test")
        spec = planner.spec_by_name["render_report"]
        result = planner.is_arg_fill_eligible(spec, ["sections"], trajectory)
        assert result is True

    def test_build_grid_items_is_eligible(self) -> None:
        """Builder payload fields are eligible for schema-aware arg-fill."""
        from penguiflow.catalog import build_catalog
        from penguiflow.planner import ReactPlanner
        from penguiflow.planner.react import Trajectory
        from penguiflow.registry import ModelRegistry
        from penguiflow.rich_output.runtime import (
            RichOutputConfig,
            attach_rich_output_nodes,
            configure_rich_output,
            reset_runtime,
        )

        reset_runtime()
        config = RichOutputConfig(enabled=True, allowlist=["grid", "markdown"])
        configure_rich_output(config)
        registry = ModelRegistry()
        catalog = build_catalog(
            list(attach_rich_output_nodes(registry, config=config)), registry
        )
        planner = ReactPlanner(
            llm_client=MockJSONLLMClient(),
            catalog=catalog,
            max_iters=1,
            arg_fill_enabled=True,
        )

        trajectory = Trajectory(query="test")
        spec = planner.spec_by_name["build_grid"]
        result = planner.is_arg_fill_eligible(spec, ["items"], trajectory)
        assert result is True

    def test_optional_simple_type_eligible(self) -> None:
        """Should be eligible for optional simple types (anyOf with null)."""
        from penguiflow.catalog import build_catalog, tool
        from penguiflow.node import Node
        from penguiflow.planner import ReactPlanner
        from penguiflow.planner.react import Trajectory
        from penguiflow.registry import ModelRegistry

        class OptionalArgs(BaseModel):
            query: str | None = None

        class OptionalOut(BaseModel):
            result: str | None = None

        @tool(desc="Tool with optional arg")
        async def optional_tool(
            args: OptionalArgs, _ctx: object
        ) -> dict[str, object]:
            return {"result": args.query}

        registry = ModelRegistry()
        registry.register("optional_tool", OptionalArgs, OptionalOut)
        catalog = build_catalog(
            [Node(optional_tool, name="optional_tool")], registry
        )
        planner = ReactPlanner(
            llm_client=MockJSONLLMClient(),
            catalog=catalog,
            max_iters=1,
            arg_fill_enabled=True,
        )

        trajectory = Trajectory(query="test")
        spec = planner.spec_by_name["optional_tool"]
        result = planner.is_arg_fill_eligible(spec, ["query"], trajectory)
        assert result is True
