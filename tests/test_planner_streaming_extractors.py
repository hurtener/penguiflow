"""Tests for planner streaming extractors."""

from __future__ import annotations

import json

from penguiflow.planner.streaming import _StreamingArgsExtractor


def _feed_in_chunks(
    extractor: _StreamingArgsExtractor, text: str, *, chunk_size: int = 7
) -> str:
    emitted: list[str] = []
    for i in range(0, len(text), chunk_size):
        emitted.extend(extractor.feed(text[i : i + chunk_size]))
    return "".join(emitted)


def test_streaming_args_extractor_streams_legacy_finish_raw_answer() -> None:
    """Streaming args extractor streams legacy finish raw answer."""
    extractor = _StreamingArgsExtractor()
    payload = json.dumps(
        {
            "thought": "done",
            "next_node": None,
            "args": {"raw_answer": "hello"},
            "plan": None,
            "join": None,
        }
    )
    emitted = _feed_in_chunks(extractor, payload)
    assert extractor.is_finish_action is True
    assert emitted == "hello"


def test_streaming_args_extractor_streams_unified_final_response_answer() -> (
    None
):
    """Streaming args extractor streams unified final response answer."""
    extractor = _StreamingArgsExtractor()
    payload = json.dumps(
        {"next_node": "final_response", "args": {"answer": "hi"}}
    )
    emitted = _feed_in_chunks(extractor, payload)
    assert extractor.is_finish_action is True
    assert emitted == "hi"


def test_streaming_args_extractor_streams_string_args() -> None:
    """Streaming args extractor streams string args."""
    extractor = _StreamingArgsExtractor()
    payload = json.dumps({"next_node": "final_response", "args": "hi"})
    emitted = _feed_in_chunks(extractor, payload)
    assert extractor.is_finish_action is True
    assert emitted == "hi"


def test_streaming_args_extractor_streams_array_args() -> None:
    """Streaming args extractor streams array args."""
    extractor = _StreamingArgsExtractor()
    payload = json.dumps({"next_node": "final_response", "args": ["hi"]})
    emitted = _feed_in_chunks(extractor, payload)
    assert extractor.is_finish_action is True
    assert emitted == "hi"


def test_streaming_args_extractor_does_not_stream_non_terminal() -> None:
    """Streaming args extractor does not stream non terminal."""
    extractor = _StreamingArgsExtractor()
    payload = json.dumps(
        {
            "thought": "tool",
            "next_node": "search",
            "args": {"q": "x"},
            "plan": None,
            "join": None,
        }
    )
    emitted = _feed_in_chunks(extractor, payload)
    assert extractor.is_finish_action is False
    assert emitted == ""
