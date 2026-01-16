"""Streaming helpers for the React planner."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from re import Pattern
from typing import Any, ClassVar


@dataclass(slots=True)
class _StreamChunk:
    """Streaming chunk captured during planning."""

    stream_id: str
    seq: int
    text: str
    done: bool
    meta: Mapping[str, Any]
    ts: float


@dataclass(slots=True)
class _ArtifactChunk:
    """Streaming artifact chunk captured during planning."""

    stream_id: str
    seq: int
    chunk: Any
    done: bool
    artifact_type: str | None
    meta: Mapping[str, Any]
    ts: float


class _JsonStringBufferExtractor:
    """Shared JSON string extraction with escape handling."""

    __slots__ = ("_buffer", "_escape_next")

    def __init__(self) -> None:
        self._buffer = ""
        self._escape_next = False

    def _extract_string_content(self, in_string_attr: str) -> list[str]:
        result: list[str] = []
        i = 0

        while i < len(self._buffer):
            char = self._buffer[i]

            if self._escape_next:
                self._escape_next = False
                if char == "n":
                    result.append("\n")
                elif char == "t":
                    result.append("\t")
                elif char == "r":
                    result.append("\r")
                elif char == '"':
                    result.append('"')
                elif char == "\\":
                    result.append("\\")
                elif char == "u" and i + 4 < len(self._buffer):
                    try:
                        hex_val = self._buffer[i + 1 : i + 5]
                        result.append(chr(int(hex_val, 16)))
                        i += 4
                    except (ValueError, IndexError):
                        result.append(char)
                else:
                    result.append(char)
                i += 1
                continue

            if char == "\\":
                self._escape_next = True
                i += 1
                continue

            if char == '"':
                setattr(self, in_string_attr, False)
                self._buffer = self._buffer[i + 1 :]
                break

            result.append(char)
            i += 1

        if getattr(self, in_string_attr):
            self._buffer = self._buffer[i:]

        return result


class _StreamingArgsExtractor(_JsonStringBufferExtractor):
    """Extracts 'args' field content from streaming JSON chunks for real-time display.

    This class buffers incoming JSON chunks and detects when the LLM is generating
    a "finish" action (next_node is null). Once detected, it extracts the args field
    content character-by-character for streaming to the UI.

    The args field is typically a dict like {"answer": "..."} or {"raw_answer": "..."},
    so we need to look for the string value inside the object.

    Uses incremental pattern matching to start streaming as early as possible:
    1. Detect "next_node": null
    2. Detect "args":
    3. Detect opening brace {
    4. Detect "answer": or "raw_answer":
    5. Detect opening quote "
    6. Start streaming content
    """

    # Pre-compiled regex patterns for streaming performance (RFC alignment)
    _RE_NEXT_NODE: ClassVar[Pattern[str]] = re.compile(r'"next_node"\s*:\s*(null|"[^"]*")')
    _RE_ARGS_KEY: ClassVar[Pattern[str]] = re.compile(r'"args"\s*:')
    _RE_ANSWER_KEY: ClassVar[Pattern[str]] = re.compile(r'"(?:answer|raw_answer)"\s*:')

    __slots__ = (
        "_is_finish_action",
        "_next_node_seen",
        "_next_node_is_non_null",
        "_in_args_string",
        "_emitted_count",
        "_found_args_key",
        "_found_args_brace",
        "_found_answer_key",
        "_found_args_array",
    )

    def __init__(self) -> None:
        super().__init__()
        self._is_finish_action = False
        self._next_node_seen = False
        self._next_node_is_non_null = False
        self._in_args_string = False  # Inside the actual string value we want to stream
        self._emitted_count = 0
        # Incremental pattern matching state
        self._found_args_key = False  # Found "args":
        self._found_args_brace = False  # Found { after "args":
        self._found_answer_key = False  # Found "answer": or "raw_answer":
        self._found_args_array = False  # Found [ after "args":

    @property
    def is_finish_action(self) -> bool:
        return self._is_finish_action

    @property
    def emitted_count(self) -> int:
        return self._emitted_count

    def feed(self, chunk: str) -> list[str]:
        """Feed a chunk of streaming JSON, return list of args content to emit.

        Returns individual characters or small strings from the args field
        that should be streamed to the UI.
        """
        self._buffer += chunk
        emits: list[str] = []

        # Detect next_node value (null or string) as early as possible.
        if not self._next_node_seen:
            next_node_match = self._RE_NEXT_NODE.search(self._buffer)
            if next_node_match:
                self._next_node_seen = True
                token = next_node_match.group(1)
                if token == "null":
                    self._is_finish_action = True
                else:
                    # Unified schema: next_node="final_response" streams args.answer
                    if token.strip('"') == "final_response":
                        self._is_finish_action = True
                    else:
                        self._next_node_is_non_null = True

        # Incremental pattern matching for args content.
        # This allows streaming to start as soon as we find the opening quote
        # instead of waiting for the entire pattern.
        if not self._next_node_is_non_null and not self._in_args_string:
            # Stage 1: Look for "args":
            if not self._found_args_key:
                args_key_match = self._RE_ARGS_KEY.search(self._buffer)
                if args_key_match:
                    self._found_args_key = True
                    self._buffer = self._buffer[args_key_match.end() :]

            # Stage 2: Look for { or [ after "args":
            if self._found_args_key and not self._found_args_brace and not self._found_args_array:
                stripped = self._buffer.lstrip()
                if stripped.startswith("{"):
                    self._found_args_brace = True
                    self._buffer = stripped[1:]  # Remove the {
                elif stripped.startswith("["):
                    self._found_args_array = True
                    self._buffer = stripped[1:]  # Remove the [

            # Stage 3: Look for "answer": or "raw_answer": in object args.
            if self._found_args_brace and not self._found_answer_key:
                answer_key_match = self._RE_ANSWER_KEY.search(self._buffer)
                if answer_key_match:
                    self._found_answer_key = True
                    self._buffer = self._buffer[answer_key_match.end() :]

            # Stage 4: Look for opening quote of the value.
            if self._found_answer_key:
                stripped = self._buffer.lstrip()
                if stripped.startswith('"'):
                    self._in_args_string = True
                    self._buffer = stripped[1:]  # Remove the opening quote
            elif self._found_args_key and not self._found_args_brace and not self._found_args_array:
                # Args can be a direct string: "args": "..."
                stripped = self._buffer.lstrip()
                if stripped.startswith('"'):
                    self._in_args_string = True
                    self._buffer = stripped[1:]
            elif self._found_args_array and not self._in_args_string:
                # Args can be an array of strings: "args": ["..."]
                stripped = self._buffer.lstrip()
                if stripped.startswith('"'):
                    self._in_args_string = True
                    self._buffer = stripped[1:]

        # Extract string content character by character
        if self._in_args_string:
            extracted = self._extract_string_content("_in_args_string")
            if extracted:
                emits.extend(extracted)
                self._emitted_count += len(extracted)

        return emits


class _StreamingThoughtExtractor(_JsonStringBufferExtractor):
    """Extracts the 'thought' field content from streaming JSON chunks.

    The thought field is intended to be short, factual execution status. The Playground
    UI renders it in a collapsible "Thinking…" panel (not as a user-facing answer).
    """

    # Pre-compiled regex pattern for thought extraction (RFC alignment)
    _RE_THOUGHT_KEY: ClassVar[Pattern[str]] = re.compile(r'"thought"\s*:\s*"')

    __slots__ = ("_in_thought_string", "_emitted_count", "_started")

    def __init__(self) -> None:
        super().__init__()
        self._in_thought_string = False
        self._emitted_count = 0
        self._started = False

    @property
    def emitted_count(self) -> int:
        return self._emitted_count

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk
        emits: list[str] = []

        if not self._started and not self._in_thought_string:
            match = self._RE_THOUGHT_KEY.search(self._buffer)
            if match:
                self._started = True
                self._in_thought_string = True
                self._buffer = self._buffer[match.end() :]

        if self._in_thought_string:
            extracted = self._extract_string_content("_in_thought_string")
            if extracted:
                emits.extend(extracted)
                self._emitted_count += len(extracted)

        return emits
