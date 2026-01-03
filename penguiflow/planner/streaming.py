"""Streaming helpers for the React planner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


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
    """

    __slots__ = ("_is_finish_action", "_in_args_string", "_emitted_count")

    def __init__(self) -> None:
        super().__init__()
        self._is_finish_action = False
        self._in_args_string = False  # Inside the actual string value we want to stream
        self._emitted_count = 0

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

        # Detect finish action by looking for "next_node": null
        if not self._is_finish_action:
            normalized = self._buffer.replace(" ", "").replace("\n", "")
            if '"next_node":null' in normalized:
                self._is_finish_action = True

        # Once we know it's a finish, look for args content
        # The args is a dict like {"answer": "..."} or {"raw_answer": "..."}
        # We need to find the string value inside
        if self._is_finish_action and not self._in_args_string:
            import re

            # Look for "args": { ... "answer"/"raw_answer": " pattern
            # Match: "args" : { "answer" : "  or "args":{"raw_answer":"
            args_value_match = re.search(r'"args"\s*:\s*\{\s*"(?:answer|raw_answer)"\s*:\s*"', self._buffer)
            if args_value_match:
                self._in_args_string = True
                # Keep only content after the opening quote of the value
                self._buffer = self._buffer[args_value_match.end() :]

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
            import re

            match = re.search(r'"thought"\s*:\s*"', self._buffer)
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
