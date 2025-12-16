"""JSON-only ReAct planner loop with pause/resume and summarisation."""

from __future__ import annotations

import inspect
import json
import logging
import time
import warnings
from collections import ChainMap, defaultdict
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, get_args, get_origin
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from ..catalog import NodeSpec, build_catalog
from ..node import Node
from ..registry import ModelRegistry
from . import prompts
from .constraints import _ConstraintTracker, _CostTracker
from .context import PlannerPauseReason, ToolContext
from .hints import _PlanningHints
from .llm import (
    _coerce_llm_response,
    _estimate_size,
    _LiteLLMJSONClient,
    _redact_artifacts,
    _sanitize_json_schema,
    _unwrap_model,
    build_messages,
    critique_answer,
    generate_clarification,
    request_revision,
    summarise_trajectory,
)
from .memory import (
    ConversationTurn,
    DefaultShortTermMemory,
    MemoryKey,
    ShortTermMemory,
    ShortTermMemoryConfig,
    TrajectoryDigest,
)
from .models import (
    ClarificationResponse,
    FinalPayload,
    JoinInjection,
    JSONLLMClient,
    ParallelCall,
    ParallelJoin,
    PlannerAction,
    PlannerEvent,
    PlannerEventCallback,
    PlannerFinish,
    PlannerPause,
    ReflectionConfig,
    ReflectionCriteria,
    ReflectionCritique,
    Source,
    ToolPolicy,
)
from .parallel import execute_parallel_plan
from .pause import _PauseRecord, _PlannerPauseSignal
from .trajectory import Trajectory, TrajectoryStep, TrajectorySummary

# Planner-specific logger
logger = logging.getLogger("penguiflow.planner")

_STM_SUMMARY_SCHEMA_NAME = "short_term_memory_summary"


class _ShortTermMemorySummary(BaseModel):
    summary: str


def _validate_llm_context(
    llm_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Ensure llm_context is JSON-serialisable."""

    if llm_context is None:
        return None
    if not isinstance(llm_context, Mapping):
        raise TypeError("llm_context must be a mapping of JSON-serializable data")
    try:
        json.dumps(llm_context, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"llm_context must be JSON-serializable: {exc}") from exc
    return dict(llm_context)


def _coerce_tool_context(
    tool_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalise tool_context to a mutable dict."""

    if tool_context is None:
        return {}
    if not isinstance(tool_context, Mapping):
        raise TypeError("tool_context must be a mapping")
    return dict(tool_context)


def _salvage_action_payload(raw: str) -> PlannerAction | None:
    """Attempt to coerce loosely-structured JSON into a PlannerAction."""

    def _lenient_parse(payload: str) -> Mapping[str, Any] | None:
        try:
            return json.loads(payload)
        except Exception:
            try:
                import ast

                maybe = ast.literal_eval(payload)
                if isinstance(maybe, Mapping):
                    return maybe
            except Exception:
                return None
        return None

    data = _lenient_parse(raw)
    if not isinstance(data, Mapping):
        return None

    patched = dict(data)
    patched.setdefault("thought", "planning next step")
    patched.setdefault("next_node", None)
    patched.setdefault("args", None)
    patched.setdefault("plan", None)
    patched.setdefault("join", None)
    if "action" in patched and isinstance(patched["action"], Mapping):
        nested = patched["action"]
        patched.update(
            {
                "thought": nested.get("thought", patched["thought"]),
                "next_node": nested.get("next_node", patched["next_node"]),
                "args": nested.get("args", patched["args"]),
                "plan": nested.get("plan", patched["plan"]),
                "join": nested.get("join", patched["join"]),
            }
        )

    # Fill args when next_node is present but args missing
    if patched.get("next_node") and patched.get("args") is None:
        patched["args"] = {}

    # Normalize plan entries
    if isinstance(patched.get("plan"), Sequence) and not isinstance(patched.get("plan"), (str, bytes, bytearray)):
        normalised_plan: list[dict[str, Any]] = []
        for item in patched["plan"]:
            if not isinstance(item, Mapping):
                continue
            entry = dict(item)
            if "node" not in entry:
                continue
            entry.setdefault("args", {})
            normalised_plan.append(entry)
        patched["plan"] = normalised_plan if normalised_plan else None

    # Normalize join shape
    if isinstance(patched.get("join"), Mapping):
        join = dict(patched["join"])
        join.setdefault("inject", None)
        join.setdefault("args", {})
        patched["join"] = join

    try:
        return PlannerAction.model_validate(patched)
    except ValidationError:
        return None


def _default_for_annotation(annotation: Any) -> Any:
    """Generate a lightweight placeholder for a required field."""

    origin = get_origin(annotation)
    if origin is Literal:
        values = get_args(annotation)
        if values:
            return values[0]

    if origin is None:
        if annotation is str:
            return "unknown"
        if annotation is bool:
            return False
        if annotation is int:
            return 0
        if annotation is float:
            return 0.0
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return {}
    else:
        if origin in (list, set, tuple, Sequence):
            return []
        if origin in (dict, Mapping):
            return {}
        if origin is type(None):
            return None

        for arg in get_args(annotation):
            if arg is type(None):
                continue
            candidate = _default_for_annotation(arg)
            if candidate is not None:
                return candidate

    return "<auto>"


def _autofill_missing_args(
    spec: NodeSpec,
    args: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]] | None:
    """Fill required args with safe defaults to avoid repeated validation loops."""

    provided: dict[str, Any] = dict(args or {})
    filled: dict[str, Any] = {}

    for field_name, field_info in spec.args_model.model_fields.items():
        if field_name in provided and provided[field_name] is not None:
            continue
        if not field_info.is_required():
            continue

        placeholder = _default_for_annotation(field_info.annotation)
        provided[field_name] = placeholder
        filled[field_name] = placeholder

    if not filled:
        return None

    return provided, tuple(filled.keys())


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


class _StreamingArgsExtractor:
    """Extracts 'args' field content from streaming JSON chunks for real-time display.

    This class buffers incoming JSON chunks and detects when the LLM is generating
    a "finish" action (next_node is null). Once detected, it extracts the args field
    content character-by-character for streaming to the UI.

    The args field is typically a dict like {"answer": "..."} or {"raw_answer": "..."},
    so we need to look for the string value inside the object.
    """

    __slots__ = ("_buffer", "_is_finish_action", "_in_args_string", "_escape_next", "_emitted_count")

    def __init__(self) -> None:
        self._buffer = ""
        self._is_finish_action = False
        self._in_args_string = False  # Inside the actual string value we want to stream
        self._escape_next = False
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
            extracted = self._extract_string_content()
            if extracted:
                emits.extend(extracted)
                self._emitted_count += len(extracted)

        return emits

    def _extract_string_content(self) -> list[str]:
        """Extract characters from a JSON string, handling escapes."""
        result: list[str] = []
        i = 0

        while i < len(self._buffer):
            char = self._buffer[i]

            if self._escape_next:
                # Handle escape sequence
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
                    # Unicode escape \uXXXX
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
                # End of string - stop processing
                self._in_args_string = False
                self._buffer = self._buffer[i + 1 :]
                break

            result.append(char)
            i += 1

        # Keep unprocessed buffer
        if self._in_args_string:
            self._buffer = self._buffer[i:]

        return result


class _StreamingThoughtExtractor:
    """Extracts the 'thought' field content from streaming JSON chunks.

    The thought field is intended to be short, factual execution status. The Playground
    UI renders it in a collapsible "Thinking…" panel (not as a user-facing answer).
    """

    __slots__ = ("_buffer", "_in_thought_string", "_escape_next", "_emitted_count", "_started")

    def __init__(self) -> None:
        self._buffer = ""
        self._in_thought_string = False
        self._escape_next = False
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
            extracted = self._extract_string_content()
            if extracted:
                emits.extend(extracted)
                self._emitted_count += len(extracted)

        return emits

    def _extract_string_content(self) -> list[str]:
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
                self._in_thought_string = False
                self._buffer = self._buffer[i + 1 :]
                break

            result.append(char)
            i += 1

        if self._in_thought_string:
            self._buffer = self._buffer[i:]

        return result


class _ArtifactCollector:
    """Collect artifact-marked fields during planner execution."""

    def __init__(self, existing: Mapping[str, Any] | None = None) -> None:
        self._artifacts: dict[str, Any] = dict(existing or {})

    def collect(
        self,
        node_name: str,
        out_model: type[BaseModel],
        observation: Mapping[str, Any],
    ) -> None:
        if not isinstance(observation, Mapping):
            return

        collected: dict[str, Any] = {}
        for field_name, field_info in out_model.model_fields.items():
            extra = field_info.json_schema_extra
            if not isinstance(extra, Mapping):
                extra = {}
            if extra.get("artifact") and field_name in observation:
                collected[field_name] = observation[field_name]

        if not collected:
            return

        existing = self._artifacts.get(node_name, {})
        merged = dict(existing)
        merged.update(collected)
        self._artifacts[node_name] = merged

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._artifacts)


def _model_json_schema_extra(model: type[BaseModel]) -> Mapping[str, Any]:
    """Return json_schema_extra from model config (ConfigDict or legacy Config)."""

    config_extra: Mapping[str, Any] | None = None
    config = getattr(model, "model_config", None)
    if isinstance(config, Mapping):
        raw_extra = config.get("json_schema_extra")
        if isinstance(raw_extra, Mapping):
            config_extra = raw_extra

    legacy_config = getattr(model, "Config", None)
    if legacy_config is not None:
        legacy_extra = getattr(legacy_config, "json_schema_extra", None)
        if isinstance(legacy_extra, Mapping):
            config_extra = {**(config_extra or {}), **legacy_extra}

    return config_extra or {}


def _produces_sources(model: type[BaseModel]) -> bool:
    """Check whether the model declares that it produces sources."""

    extra = _model_json_schema_extra(model)
    return bool(extra.get("produces_sources"))


def _source_field_map(model: type[BaseModel]) -> dict[str, str]:
    """Build mapping of model field names to Source fields."""

    mapping: dict[str, str] = {}
    for field_name, field_info in model.model_fields.items():
        extra = field_info.json_schema_extra
        if not isinstance(extra, Mapping):
            extra = {}
        target = extra.get("source_field")
        if target is None and field_name in Source.model_fields:
            target = field_name
        if target:
            mapping[field_name] = str(target)
    return mapping


def _extract_source_payloads(
    out_model: type[BaseModel],
    observation: Any,
) -> list[Mapping[str, Any]]:
    """Extract potential Source payloads from an observation."""

    if observation is None:
        return []
    if isinstance(observation, BaseModel):
        observation = observation.model_dump(mode="json")
    if not isinstance(observation, Mapping):
        return []

    payloads: list[Mapping[str, Any]] = []

    if _produces_sources(out_model):
        mapping = _source_field_map(out_model)
        if mapping:
            payload = {
                target: observation.get(field_name)
                for field_name, target in mapping.items()
                if field_name in observation
            }
            if payload:
                payloads.append(payload)

    for field_name, field_info in out_model.model_fields.items():
        nested_model = _unwrap_model(field_info.annotation)
        if nested_model is None:
            continue
        nested_value = observation.get(field_name)
        if nested_value is None:
            continue

        if isinstance(nested_value, Sequence) and not isinstance(nested_value, (str, bytes, bytearray, Mapping)):
            for item in nested_value:
                payloads.extend(_extract_source_payloads(nested_model, item))
        elif isinstance(nested_value, Mapping) or isinstance(nested_value, BaseModel):
            payloads.extend(_extract_source_payloads(nested_model, nested_value))

    return payloads


class _SourceCollector:
    """Collect Source objects emitted by tools during execution."""

    def __init__(self, existing: Sequence[Mapping[str, Any]] | None = None) -> None:
        self._sources: list[Source] = []
        self._seen: set[tuple[str, str | None, str | None]] = set()
        for src in existing or []:
            self._add(src)

    def _add(self, payload: Mapping[str, Any] | Source) -> None:
        try:
            model = payload if isinstance(payload, Source) else Source.model_validate(payload)
        except ValidationError as exc:
            logger.debug("source_validation_failed", extra={"error": str(exc)})
            return

        key = (model.title, model.url, model.snippet)
        if key in self._seen:
            return
        self._seen.add(key)
        self._sources.append(model)

    def collect(self, out_model: type[BaseModel], observation: Mapping[str, Any]) -> None:
        if not isinstance(observation, Mapping):
            return
        for payload in _extract_source_payloads(out_model, observation):
            self._add(payload)

    def snapshot(self) -> list[Mapping[str, Any]]:
        return [src.model_dump(mode="json") for src in self._sources]


def _normalise_artifact_value(value: Any) -> Any:
    """Best-effort conversion of artifact chunks to JSON-serialisable payloads."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        try:
            return json.loads(json.dumps(value, default=str, ensure_ascii=False))
        except Exception:
            return repr(value)


def _fallback_answer(last_observation: Any) -> str:
    """Provide a safe fallback answer when planner args are missing.

    This function extracts a human-readable answer string from various payload formats.
    Per RFC_STRUCTURED_PLANNER_OUTPUT, the result must be a plain string (not JSON).
    """

    if isinstance(last_observation, Mapping):
        # First pass: check for answer-like keys (prioritized order)
        for key in (
            "raw_answer",
            "answer",
            "text",
            "result",
            "output",
            "response",
            "message",
            "content",
            "greeting",
            "joke",
            "reply",
            "summary",
            "explanation",
            "description",
            "body",
        ):
            if key in last_observation:
                value = last_observation[key]
                if isinstance(value, str):
                    return value
                if isinstance(value, Mapping):
                    # Recursively extract from nested dict
                    return _fallback_answer(value)
                if value is not None:
                    return str(value)

        # Second pass: check if there's a nested 'args' dict with answer-like keys
        if "args" in last_observation and isinstance(last_observation["args"], Mapping):
            nested = _fallback_answer(last_observation["args"])
            if nested != "No answer produced.":
                return nested

        # Third pass: if observation has exactly one string value > 10 chars, use it
        # (excluding 'thought' and 'next_node' which are planner metadata)
        excluded_keys = {"thought", "next_node", "plan", "join"}
        string_values = [
            v for k, v in last_observation.items() if k not in excluded_keys and isinstance(v, str) and len(v) > 10
        ]
        if len(string_values) == 1:
            return string_values[0]

        # Fourth pass: use 'thought' as last resort if it looks like an answer
        # (i.e., it doesn't start with typical thinking phrases)
        thought = last_observation.get("thought", "")
        if isinstance(thought, str) and len(thought) > 20:
            thinking_phrases = (
                "i need to",
                "i should",
                "i will",
                "let me",
                "i'll",
                "first,",
                "now i",
                "the user",
                "based on",
                "looking at",
                "i can see",
                "i notice",
                "according to",
            )
            thought_lower = thought.lower().strip()
            if not any(thought_lower.startswith(p) for p in thinking_phrases):
                return thought

    if isinstance(last_observation, str):
        return last_observation
    if last_observation is not None:
        try:
            return json.dumps(last_observation, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(last_observation)
    return "No answer produced."


class _PlannerContext(ToolContext):
    __slots__ = (
        "_llm_context",
        "_tool_context",
        "_planner",
        "_trajectory",
        "_chunks",
        "_artifact_chunks",
        "_artifact_seq",
        "_meta_warned",
    )

    def __init__(self, planner: ReactPlanner, trajectory: Trajectory) -> None:
        self._llm_context = dict(trajectory.llm_context or {})
        self._tool_context = dict(trajectory.tool_context or {})
        self._planner = planner
        self._trajectory = trajectory
        self._chunks: list[_StreamChunk] = []
        self._artifact_chunks: list[_ArtifactChunk] = []
        self._artifact_seq: defaultdict[str, int] = defaultdict(int)
        self._meta_warned = False

    @property
    def llm_context(self) -> Mapping[str, Any]:
        return MappingProxyType(self._llm_context)

    @property
    def tool_context(self) -> dict[str, Any]:
        return self._tool_context

    @property
    def meta(self) -> MutableMapping[str, Any]:
        if not self._meta_warned:
            warnings.warn(
                "ctx.meta is deprecated; use ctx.llm_context and ctx.tool_context instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            self._meta_warned = True
        return ChainMap(self._tool_context, self._llm_context)

    async def emit_chunk(
        self,
        stream_id: str,
        seq: int,
        text: str,
        *,
        done: bool = False,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit streaming chunk during tool execution."""

        combined_meta = {"channel": "thinking"}
        combined_meta.update(meta or {})

        chunk = _StreamChunk(
            stream_id=stream_id,
            seq=seq,
            text=text,
            done=done,
            meta=dict(combined_meta),
            ts=self._planner._time_source(),
        )
        self._chunks.append(chunk)

        self._planner._emit_event(
            PlannerEvent(
                event_type="stream_chunk",
                ts=chunk.ts,
                trajectory_step=len(self._trajectory.steps),
                extra={
                    "stream_id": stream_id,
                    "seq": seq,
                    "text": text,
                    "done": done,
                    "meta": dict(combined_meta),
                },
            )
        )

    async def emit_artifact(
        self,
        stream_id: str,
        chunk: Any,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit a streaming artifact chunk during tool execution."""

        serialised_chunk = _normalise_artifact_value(chunk)
        seq = self._artifact_seq[stream_id]
        self._artifact_seq[stream_id] += 1
        record = _ArtifactChunk(
            stream_id=stream_id,
            seq=seq,
            chunk=serialised_chunk,
            done=done,
            artifact_type=artifact_type or type(chunk).__name__,
            meta=dict(meta or {}),
            ts=self._planner._time_source(),
        )
        self._artifact_chunks.append(record)

        self._planner._emit_event(
            PlannerEvent(
                event_type="artifact_chunk",
                ts=record.ts,
                trajectory_step=len(self._trajectory.steps),
                extra={
                    "stream_id": stream_id,
                    "seq": seq,
                    "chunk": serialised_chunk,
                    "done": done,
                    "artifact_type": record.artifact_type,
                    "meta": dict(meta or {}),
                },
            )
        )

    def _collect_chunks(self) -> dict[str, list[dict[str, Any]]]:
        """Collect streaming chunks grouped by stream identifier."""

        if not self._chunks and not self._artifact_chunks:
            return {}

        streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for chunk in self._chunks:
            streams[chunk.stream_id].append(
                {
                    "seq": chunk.seq,
                    "text": chunk.text,
                    "done": chunk.done,
                    "meta": dict(chunk.meta),
                    "ts": chunk.ts,
                }
            )
        for artifact in self._artifact_chunks:
            streams[artifact.stream_id].append(
                {
                    "seq": artifact.seq,
                    "chunk": artifact.chunk,
                    "artifact_type": artifact.artifact_type,
                    "done": artifact.done,
                    "meta": dict(artifact.meta),
                    "ts": artifact.ts,
                }
            )

        for stream_chunks in streams.values():
            stream_chunks.sort(key=lambda payload: payload["seq"])

        self._chunks.clear()
        self._artifact_chunks.clear()
        self._artifact_seq.clear()
        return dict(streams)

    async def pause(
        self,
        reason: PlannerPauseReason,
        payload: Mapping[str, Any] | None = None,
    ) -> PlannerPause:
        return await self._planner._pause_from_context(
            reason,
            dict(payload or {}),
            self._trajectory,
        )


class ReactPlanner:
    """JSON-only ReAct planner for autonomous multi-step workflows.

    The ReactPlanner orchestrates a loop where an LLM selects and sequences
    PenguiFlow nodes/tools based on structured JSON contracts. It supports
    pause/resume for approvals, adaptive re-planning on failures, parallel
    execution, and trajectory compression for long-running sessions.

    Thread Safety
    -------------
    NOT thread-safe. Create separate planner instances per task.

    Parameters
    ----------
    llm : str | Mapping[str, Any] | None
        LiteLLM model name (e.g., "gpt-4") or config dict. Required if
        llm_client is not provided.
    nodes : Sequence[Node] | None
        Sequence of PenguiFlow nodes to make available as tools. Either
        (nodes + registry) or catalog must be provided.
    catalog : Sequence[NodeSpec] | None
        Pre-built tool catalog. If provided, nodes and registry are ignored.
    registry : ModelRegistry | None
        Model registry for type resolution. Required if nodes is provided.
    llm_client : JSONLLMClient | None
        Custom LLM client implementation. If provided, llm is ignored.
    max_iters : int
        Maximum planning iterations before returning no_path. Default: 8.
    temperature : float
        LLM sampling temperature. Default: 0.0 for deterministic output.
    json_schema_mode : bool
        Enable strict JSON schema enforcement via LLM response_format.
        Default: True.
    system_prompt_extra : str | None
        Optional instructions for interpreting custom context (e.g., memory format).
        Use this to specify how the planner should use structured data passed via
        llm_context. The library provides baseline injection; this parameter lets
        you define format-specific semantics.

        Examples:
        - "memories contains JSON with user preferences; respect them when planning"
        - "context.knowledge is a flat list of facts; cite relevant ones"
        - "Use context.history to avoid repeating failed approaches"
    token_budget : int | None
        If set, triggers trajectory summarization when history exceeds limit.
        Token count is estimated by character length (approx).
    pause_enabled : bool
        Allow nodes to trigger pause/resume flow. Default: True.
    state_store : StateStore | None
        Optional durable state adapter for pause/resume persistence.
    summarizer_llm : str | Mapping[str, Any] | None
        Separate (cheaper) LLM for trajectory compression. Falls back to
        main LLM if not set.
    reflection_config : ReflectionConfig | None
        Optional configuration enabling automatic answer critique before
        finishing. Disabled by default.
    reflection_llm : str | Mapping[str, Any] | None
        Optional LiteLLM identifier used for critique when
        ``reflection_config.use_separate_llm`` is ``True``.
    planning_hints : Mapping[str, Any] | None
        Structured constraints and preferences (ordering, disallowed nodes,
        max_parallel, etc.). See plan.md for schema.
    tool_policy : ToolPolicy | None
        Optional runtime policy that filters the tool catalog (whitelists,
        blacklists, or tag requirements) for multi-tenant and safety use cases.
    repair_attempts : int
        Max attempts to repair invalid JSON from LLM. Default: 3.
    deadline_s : float | None
        Wall-clock deadline for planning session (seconds from start).
    hop_budget : int | None
        Maximum tool invocations allowed.
    time_source : Callable[[], float] | None
        Override time.monotonic for testing.
    event_callback : PlannerEventCallback | None
        Optional callback receiving PlannerEvent instances for observability.
    llm_timeout_s : float
        Per-LLM-call timeout in seconds. Default: 60.0.
    llm_max_retries : int
        Max retry attempts for transient LLM failures. Default: 3.
    absolute_max_parallel : int
        System-level safety limit on parallel execution regardless of hints.
        Default: 50.

    Raises
    ------
    ValueError
        If neither (nodes + registry) nor catalog is provided, or if neither
        llm nor llm_client is provided.
    RuntimeError
        If LiteLLM is not installed and llm_client is not provided.

    Examples
    --------
    >>> planner = ReactPlanner(
    ...     llm="gpt-4",
    ...     nodes=[triage_node, retrieve_node, summarize_node],
    ...     registry=my_registry,
    ...     max_iters=10,
    ... )
    >>> result = await planner.run("Explain PenguiFlow's architecture")
    >>> print(result.reason)  # "answer_complete", "no_path", or "budget_exhausted"
    """

    # Default system-level safety limit for parallel execution
    DEFAULT_MAX_PARALLEL = 50

    def __init__(
        self,
        llm: str | Mapping[str, Any] | None = None,
        *,
        nodes: Sequence[Node] | None = None,
        catalog: Sequence[NodeSpec] | None = None,
        registry: ModelRegistry | None = None,
        llm_client: JSONLLMClient | None = None,
        max_iters: int = 8,
        temperature: float = 0.0,
        json_schema_mode: bool = True,
        system_prompt_extra: str | None = None,
        token_budget: int | None = None,
        pause_enabled: bool = True,
        state_store: Any | None = None,
        summarizer_llm: str | Mapping[str, Any] | None = None,
        planning_hints: Mapping[str, Any] | None = None,
        repair_attempts: int = 3,
        deadline_s: float | None = None,
        hop_budget: int | None = None,
        time_source: Callable[[], float] | None = None,
        event_callback: PlannerEventCallback | None = None,
        llm_timeout_s: float = 60.0,
        llm_max_retries: int = 3,
        absolute_max_parallel: int = 50,
        reflection_config: ReflectionConfig | None = None,
        reflection_llm: str | Mapping[str, Any] | None = None,
        tool_policy: ToolPolicy | None = None,
        stream_final_response: bool = False,
        short_term_memory: ShortTermMemory | ShortTermMemoryConfig | None = None,
    ) -> None:
        if catalog is None:
            if nodes is None or registry is None:
                raise ValueError("Either catalog or (nodes and registry) must be provided")
            catalog = build_catalog(nodes, registry)

        self._stream_final_response = stream_final_response
        self._tool_policy = tool_policy
        specs = list(catalog)
        if tool_policy is not None:
            filtered_specs: list[NodeSpec] = []
            removed: list[str] = []
            for spec in specs:
                if tool_policy.is_allowed(spec.name, spec.tags):
                    filtered_specs.append(spec)
                else:
                    removed.append(spec.name)

            if removed:
                logger.info(
                    "planner_tool_policy_filtered",
                    extra={
                        "removed": removed,
                        "original_count": len(specs),
                        "filtered_count": len(filtered_specs),
                    },
                )
            if not filtered_specs:
                logger.warning(
                    "planner_tool_policy_empty",
                    extra={"original_count": len(specs)},
                )
            specs = filtered_specs

        self._specs = specs
        self._spec_by_name = {spec.name: spec for spec in self._specs}
        self._catalog_records = [spec.to_tool_record() for spec in self._specs]
        self._planning_hints = _PlanningHints.from_mapping(planning_hints)
        hints_payload = self._planning_hints.to_prompt_payload() if not self._planning_hints.empty() else None
        self._system_prompt = prompts.build_system_prompt(
            self._catalog_records,
            extra=system_prompt_extra,
            planning_hints=hints_payload,
        )
        self._max_iters = max_iters
        self._repair_attempts = repair_attempts
        self._json_schema_mode = json_schema_mode
        self._token_budget = token_budget
        self._pause_enabled = pause_enabled
        self._state_store = state_store
        self._pause_records: dict[str, _PauseRecord] = {}
        self._active_trajectory: Trajectory | None = None
        self._active_tracker: _ConstraintTracker | None = None
        self._cost_tracker = _CostTracker()
        self._deadline_s = deadline_s
        self._hop_budget = hop_budget
        self._time_source = time_source or time.monotonic
        self._event_callback = event_callback
        self._absolute_max_parallel = absolute_max_parallel
        action_schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "planner_action",
                "schema": PlannerAction.model_json_schema(),
            },
        }
        self._action_schema: Mapping[str, Any] = action_schema
        self._response_format = action_schema if json_schema_mode else None
        self._summarizer_client: JSONLLMClient | None = None
        self._reflection_client: JSONLLMClient | None = None
        self._clarification_client: JSONLLMClient | None = None
        self._reflection_config = reflection_config
        self._action_seq = 0
        self._ready_answer_seq: int | None = None

        self._memory_config = ShortTermMemoryConfig()
        self._memory_singleton: ShortTermMemory | None = None
        self._memory_by_key: dict[str, ShortTermMemory] = {}
        self._memory_ephemeral_key: MemoryKey | None = None
        self._memory_summarizer_client: JSONLLMClient | None = None
        self._memory_summarizer: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]] | None = None
        if isinstance(short_term_memory, ShortTermMemoryConfig):
            self._memory_config = short_term_memory
            if short_term_memory.strategy != "none":
                # Default memory is scoped per session key to prevent leakage across users/sessions.
                self._memory_by_key = {}
        elif short_term_memory is not None:
            # Custom memory instances are assumed to manage their own isolation semantics.
            self._memory_singleton = short_term_memory

        if llm_client is not None:
            self._client = llm_client

            # CRITICAL: Detect DSPy client and create separate instances for multi-schema support
            # DSPyLLMClient is hardcoded to a single output schema, so we need separate
            # instances for reflection (ReflectionCritique), summarization (TrajectorySummary),
            # and clarification (ClarificationResponse)
            from .dspy_client import DSPyLLMClient

            is_dspy = isinstance(llm_client, DSPyLLMClient)

            # Create DSPy reflection client if reflection enabled
            if is_dspy and reflection_config and reflection_config.enabled:
                assert isinstance(llm_client, DSPyLLMClient)  # for mypy
                logger.info(
                    "dspy_reflection_client_creation",
                    extra={"schema": "ReflectionCritique"},
                )
                self._reflection_client = DSPyLLMClient.from_base_client(llm_client, ReflectionCritique)

                # Create DSPy clarification client (used when reflection fails)
                logger.info(
                    "dspy_clarification_client_creation",
                    extra={"schema": "ClarificationResponse"},
                )
                self._clarification_client = DSPyLLMClient.from_base_client(llm_client, ClarificationResponse)

            # Create DSPy summarizer client if summarization enabled
            if is_dspy and token_budget is not None and token_budget > 0:
                assert isinstance(llm_client, DSPyLLMClient)  # for mypy
                logger.info(
                    "dspy_summarizer_client_creation",
                    extra={"schema": "TrajectorySummary"},
                )
                self._summarizer_client = DSPyLLMClient.from_base_client(llm_client, TrajectorySummary)

            if is_dspy and self._memory_singleton is None and self._memory_config.strategy == "rolling_summary":
                assert isinstance(llm_client, DSPyLLMClient)  # for mypy
                logger.info(
                    "dspy_memory_summarizer_client_creation",
                    extra={"schema": "ShortTermMemorySummary"},
                )
                if self._memory_config.summarizer_model:
                    self._memory_summarizer_client = DSPyLLMClient(
                        llm=self._memory_config.summarizer_model,
                        output_schema=_ShortTermMemorySummary,
                        temperature=temperature,
                        max_retries=llm_max_retries,
                        timeout_s=llm_timeout_s,
                    )
                else:
                    self._memory_summarizer_client = DSPyLLMClient.from_base_client(
                        llm_client,
                        _ShortTermMemorySummary,
                    )
        else:
            if llm is None:
                raise ValueError("llm or llm_client must be provided")
            self._client = _LiteLLMJSONClient(
                llm,
                temperature=temperature,
                json_schema_mode=json_schema_mode,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
                streaming_enabled=stream_final_response,
            )

        if (
            self._memory_summarizer_client is None
            and self._memory_singleton is None
            and self._memory_config.strategy == "rolling_summary"
            and self._memory_config.summarizer_model is not None
        ):
            self._memory_summarizer_client = _LiteLLMJSONClient(
                self._memory_config.summarizer_model,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )

        # LiteLLM-based separate clients (override DSPy if explicitly provided)
        if summarizer_llm is not None:
            self._summarizer_client = _LiteLLMJSONClient(
                summarizer_llm,
                temperature=temperature,
                json_schema_mode=True,
                max_retries=llm_max_retries,
                timeout_s=llm_timeout_s,
            )

        # Only set reflection client from reflection_llm if not already set by DSPy
        if self._reflection_client is None:
            if reflection_config and reflection_config.use_separate_llm:
                if reflection_llm is None:
                    raise ValueError("reflection_llm required when use_separate_llm=True")
                self._reflection_client = _LiteLLMJSONClient(
                    reflection_llm,
                    temperature=temperature,
                    json_schema_mode=True,
                    max_retries=llm_max_retries,
                    timeout_s=llm_timeout_s,
                )

    async def run(
        self,
        query: str,
        *,
        llm_context: Mapping[str, Any] | None = None,
        context_meta: Mapping[str, Any] | None = None,  # Deprecated
        tool_context: Mapping[str, Any] | None = None,
        memory_key: MemoryKey | None = None,
    ) -> PlannerFinish | PlannerPause:
        """Execute planner on a query until completion or pause.

        Parameters
        ----------
        query : str
            Natural language task description.
        llm_context : Mapping[str, Any] | None
            Optional context visible to LLM (memories, status_history, etc.).
            Should NOT include internal metadata like tenant_id or trace_id.
        context_meta : Mapping[str, Any] | None
            **Deprecated**: Use llm_context instead. This parameter is kept for
            backward compatibility but will be removed in a future version.
        tool_context : Mapping[str, Any] | None
            Tool-only context (callbacks, loggers, telemetry objects). Not
            visible to the LLM. May contain non-serialisable objects.
        memory_key : MemoryKey | None
            Optional explicit short-term memory key. If omitted, the planner may
            derive a key from `tool_context` using the configured memory isolation
            paths. If no key is available and memory is configured to require an
            explicit key, memory behaves as disabled for this call.

        Returns
        -------
        PlannerFinish | PlannerPause
            PlannerFinish if task completed/failed, PlannerPause if paused
            for human intervention.

        Raises
        ------
        RuntimeError
            If LLM client fails after all retries.
        """
        # Handle backward compatibility
        if context_meta is not None:
            warnings.warn(
                "context_meta parameter is deprecated. Use llm_context instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            if llm_context is None:
                llm_context = context_meta

        logger.info("planner_run_start", extra={"query": query})
        normalised_tool_context = _coerce_tool_context(tool_context)
        normalised_llm_context = _validate_llm_context(llm_context)
        resolved_key = self._resolve_memory_key(memory_key, normalised_tool_context)
        normalised_llm_context = await self._apply_memory_context(normalised_llm_context, resolved_key)
        self._cost_tracker = _CostTracker()
        trajectory = Trajectory(
            query=query,
            llm_context=normalised_llm_context,
            tool_context=normalised_tool_context,
        )
        result = await self._run_loop(trajectory, tracker=None)
        await self._maybe_record_memory_turn(query, result, trajectory, resolved_key)
        return result

    async def resume(
        self,
        token: str,
        user_input: str | None = None,
        *,
        tool_context: Mapping[str, Any] | None = None,
        memory_key: MemoryKey | None = None,
    ) -> PlannerFinish | PlannerPause:
        """Resume a paused planning session.

        Parameters
        ----------
        token : str
            Resume token from a previous PlannerPause.
        user_input : str | None
            Optional user response to the pause (e.g., approval decision).
        tool_context : Mapping[str, Any] | None
            Tool-only context (callbacks, loggers, telemetry objects). Not
            visible to the LLM. May contain non-serialisable objects. Overrides
            any tool_context captured in the pause record.
        memory_key : MemoryKey | None
            Optional explicit short-term memory key for the resumed session. If
            omitted, the planner may derive a key from `tool_context` using the
            configured memory isolation paths. If no key is available and memory
            is configured to require an explicit key, memory behaves as disabled
            for this call.

        Returns
        -------
        PlannerFinish | PlannerPause
            Updated result after resuming execution.

        Raises
        ------
        KeyError
            If resume token is invalid or expired.
        """
        logger.info("planner_resume", extra={"token": token[:8] + "..."})
        provided_tool_context = _coerce_tool_context(tool_context) if tool_context is not None else None
        record = await self._load_pause_record(token)
        trajectory = record.trajectory
        trajectory.llm_context = _validate_llm_context(trajectory.llm_context) or {}
        if provided_tool_context is not None:
            trajectory.tool_context = provided_tool_context
        elif record.tool_context is not None:
            trajectory.tool_context = dict(record.tool_context)
        else:
            trajectory.tool_context = trajectory.tool_context or {}
        if user_input is not None:
            trajectory.resume_user_input = user_input

        resolved_key = self._resolve_memory_key(memory_key, trajectory.tool_context or {})
        merged_llm_context = await self._apply_memory_context(
            dict(trajectory.llm_context or {}),
            resolved_key,
        )
        trajectory.llm_context = merged_llm_context
        tracker: _ConstraintTracker | None = None
        if record.constraints is not None:
            tracker = _ConstraintTracker.from_snapshot(
                record.constraints,
                time_source=self._time_source,
            )

        # Emit resume event
        self._emit_event(
            PlannerEvent(
                event_type="resume",
                ts=self._time_source(),
                trajectory_step=len(trajectory.steps),
                extra={"user_input": user_input} if user_input else {},
            )
        )

        result = await self._run_loop(trajectory, tracker=tracker)
        await self._maybe_record_memory_turn(trajectory.query, result, trajectory, resolved_key)
        return result

    def _resolve_memory_key(
        self,
        explicit: MemoryKey | None,
        tool_context: Mapping[str, Any] | None,
    ) -> MemoryKey | None:
        if self._memory_singleton is None and self._memory_config.strategy == "none":
            return None
        if explicit is not None:
            return explicit
        extracted = self._extract_memory_key_from_tool_context(tool_context or {})
        if extracted is not None:
            return extracted
        if self._memory_config.isolation.require_explicit_key:
            return None
        if self._memory_ephemeral_key is None:
            self._memory_ephemeral_key = MemoryKey(
                tenant_id="default",
                user_id="anonymous",
                session_id=uuid4().hex,
            )
        return self._memory_ephemeral_key

    def _get_memory_for_key(self, key: MemoryKey) -> ShortTermMemory | None:
        if self._memory_singleton is not None:
            return self._memory_singleton
        if self._memory_config.strategy == "none":
            return None
        composite = key.composite()
        memory = self._memory_by_key.get(composite)
        if memory is None:
            summarizer = None
            if self._memory_config.strategy == "rolling_summary":
                summarizer = self._get_short_term_memory_summarizer()
            memory = DefaultShortTermMemory(config=self._memory_config, summarizer=summarizer)
            self._memory_by_key[composite] = memory
        return memory

    @staticmethod
    def _normalise_session_summary(summary: str) -> str:
        summary = summary.strip()
        if not summary:
            return "<session_summary></session_summary>"
        if "<session_summary>" not in summary:
            return f"<session_summary>\n{summary}\n</session_summary>"
        return summary

    def _get_short_term_memory_summarizer(self) -> Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]:
        if self._memory_summarizer is not None:
            return self._memory_summarizer

        async def _summarize(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            previous_summary = str(payload.get("previous_summary") or "")
            turns = payload.get("turns") or []
            if not isinstance(turns, Sequence):
                raise TypeError("turns must be a sequence")

            logger.debug(
                "memory_summarizer_call_start",
                extra={
                    "turns_count": len(turns),
                    "previous_summary_len": len(previous_summary),
                    "has_dedicated_client": self._memory_summarizer_client is not None,
                },
            )

            client = self._memory_summarizer_client or self._client
            messages = prompts.build_short_term_memory_summary_messages(
                previous_summary=previous_summary,
                turns=[dict(item) for item in turns if isinstance(item, Mapping)],
            )
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": _STM_SUMMARY_SCHEMA_NAME,
                    "schema": _sanitize_json_schema(_ShortTermMemorySummary.model_json_schema()),
                },
            }
            llm_result = await client.complete(messages=messages, response_format=response_format)
            raw, _ = _coerce_llm_response(llm_result)
            parsed = _ShortTermMemorySummary.model_validate_json(raw)
            summary = self._normalise_session_summary(parsed.summary)

            logger.debug(
                "memory_summarizer_call_complete",
                extra={
                    "summary_len": len(summary),
                    "turns_processed": len(turns),
                },
            )

            return {"summary": summary}

        self._memory_summarizer = _summarize
        return self._memory_summarizer

    @staticmethod
    def _extract_path(mapping: Mapping[str, Any], path: str) -> Any | None:
        current: Any = mapping
        for part in path.split("."):
            if not isinstance(current, Mapping):
                return None
            if part not in current:
                return None
            current = current[part]
        return current

    def _extract_memory_key_from_tool_context(self, tool_context: Mapping[str, Any]) -> MemoryKey | None:
        isolation = self._memory_config.isolation
        tenant_value = self._extract_path(tool_context, isolation.tenant_key)
        user_value = self._extract_path(tool_context, isolation.user_key)
        session_value = self._extract_path(tool_context, isolation.session_key)
        if session_value is None or str(session_value).strip() == "":
            return None
        tenant_id = str(tenant_value).strip() if tenant_value is not None else "default"
        user_id = str(user_value).strip() if user_value is not None else "anonymous"
        return MemoryKey(tenant_id=tenant_id, user_id=user_id, session_id=str(session_value).strip())

    async def _apply_memory_context(
        self,
        llm_context: dict[str, Any] | None,
        key: MemoryKey | None,
    ) -> dict[str, Any] | None:
        if key is None:
            return llm_context
        memory = self._get_memory_for_key(key)
        if memory is None:
            return llm_context
        await self._maybe_memory_hydrate(memory, key)
        try:
            patch = await memory.get_llm_context()
        except Exception as exc:
            logger.warning(
                "memory_get_llm_context_failed",
                extra={
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )
            return llm_context
        if not patch:
            return llm_context
        merged: dict[str, Any] = dict(llm_context or {})
        merged.update(dict(patch))
        try:
            json.dumps(merged, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "memory_context_not_json_serialisable",
                extra={
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )
            return llm_context
        return merged

    async def _maybe_memory_hydrate(self, memory: ShortTermMemory, key: MemoryKey) -> None:
        if self._state_store is None:
            return
        hydrate = getattr(memory, "hydrate", None)
        if hydrate is None:
            return
        try:
            await hydrate(self._state_store, key.composite())
        except Exception as exc:
            logger.warning(
                "memory_hydrate_failed",
                extra={"error": str(exc), "error_type": exc.__class__.__name__},
            )

    async def _maybe_memory_persist(self, memory: ShortTermMemory, key: MemoryKey) -> None:
        if self._state_store is None:
            return
        persist = getattr(memory, "persist", None)
        if persist is None:
            return
        try:
            await persist(self._state_store, key.composite())
        except Exception as exc:
            logger.warning(
                "memory_persist_failed",
                extra={"error": str(exc), "error_type": exc.__class__.__name__},
            )

    def _build_memory_turn(self, query: str, result: PlannerFinish, trajectory: Trajectory) -> ConversationTurn:
        payload = result.payload
        if isinstance(payload, Mapping):
            assistant = payload.get("raw_answer")
            assistant_response = assistant if isinstance(assistant, str) else json.dumps(payload, ensure_ascii=False)
        else:
            assistant_response = str(payload) if payload is not None else ""

        digest: TrajectoryDigest | None = None
        if self._memory_config.include_trajectory_digest:
            tools: list[str] = []
            obs_lines: list[str] = []
            for step in trajectory.steps:
                tool_name = step.action.next_node
                if tool_name is None:
                    continue
                if step.error is not None or step.observation is None:
                    continue
                tools.append(tool_name)
                try:
                    obs_payload = step.serialise_for_llm()
                    obs_text = json.dumps(obs_payload, ensure_ascii=False)
                except Exception:
                    obs_text = str(step.serialise_for_llm())
                if len(obs_text) > 400:
                    obs_text = obs_text[:400] + "…"
                obs_lines.append(f"- {tool_name}: {obs_text}")

            if tools:
                thought = result.metadata.get("thought")
                digest = TrajectoryDigest(
                    tools_invoked=tools,
                    observations_summary="\n".join(obs_lines),
                    reasoning_summary=thought if isinstance(thought, str) else None,
                )

        return ConversationTurn(
            user_message=query,
            assistant_response=assistant_response,
            trajectory_digest=digest,
            ts=time.time(),
        )

    async def _maybe_record_memory_turn(
        self,
        query: str,
        result: PlannerFinish | PlannerPause,
        trajectory: Trajectory,
        key: MemoryKey | None,
    ) -> None:
        if key is None:
            return
        memory = self._get_memory_for_key(key)
        if memory is None:
            return
        if not isinstance(result, PlannerFinish):
            return
        turn = self._build_memory_turn(query, result, trajectory)
        try:
            await memory.add_turn(turn)
        except Exception as exc:
            logger.warning(
                "memory_add_turn_failed",
                extra={
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )
            return
        await self._maybe_memory_persist(memory, key)

    async def _run_loop(
        self,
        trajectory: Trajectory,
        *,
        tracker: _ConstraintTracker | None,
    ) -> PlannerFinish | PlannerPause:
        last_observation: Any | None = None
        artifact_collector = _ArtifactCollector(trajectory.artifacts)
        source_collector = _SourceCollector(trajectory.sources)
        self._active_trajectory = trajectory
        if tracker is None:
            tracker = _ConstraintTracker(
                deadline_s=self._deadline_s,
                hop_budget=self._hop_budget,
                time_source=self._time_source,
            )
        self._active_tracker = tracker
        try:
            while len(trajectory.steps) < self._max_iters:
                deadline_message = tracker.check_deadline()
                if deadline_message is not None:
                    logger.warning(
                        "deadline_exhausted",
                        extra={"step": len(trajectory.steps)},
                    )
                    trajectory.artifacts = artifact_collector.snapshot()
                    trajectory.sources = source_collector.snapshot()
                    return self._finish(
                        trajectory,
                        reason="budget_exhausted",
                        payload=last_observation,
                        thought=deadline_message,
                        constraints=tracker,
                    )

                # Emit step start event and bump action sequence
                step_start_ts = self._time_source()
                self._action_seq += 1
                current_action_seq = self._action_seq
                self._emit_event(
                    PlannerEvent(
                        event_type="step_start",
                        ts=step_start_ts,
                        trajectory_step=len(trajectory.steps),
                        extra={"action_seq": current_action_seq},
                    )
                )

                action = await self.step(trajectory)

                # Log the action received from LLM
                logger.info(
                    "planner_action",
                    extra={
                        "step": len(trajectory.steps),
                        "thought": action.thought,
                        "next_node": action.next_node,
                        "has_plan": action.plan is not None,
                    },
                )

                # Check constraints BEFORE executing parallel plan or any action
                constraint_error = self._check_action_constraints(action, trajectory, tracker)
                if constraint_error is not None:
                    trajectory.steps.append(TrajectoryStep(action=action, error=constraint_error))
                    trajectory.summary = None
                    continue

                if action.plan:
                    parallel_observation, pause = await self._execute_parallel_plan(
                        action,
                        trajectory,
                        tracker,
                        artifact_collector,
                        source_collector,
                    )
                    if pause is not None:
                        return pause
                    trajectory.summary = None
                    last_observation = parallel_observation
                    trajectory.artifacts = artifact_collector.snapshot()
                    trajectory.sources = source_collector.snapshot()
                    trajectory.resume_user_input = None
                    continue

                if action.next_node is None:
                    candidate_answer = action.args or last_observation
                    metadata_reflection: dict[str, Any] | None = None

                    if candidate_answer is not None and self._reflection_config and self._reflection_config.enabled:
                        critique: ReflectionCritique | None = None
                        metadata_reflection = {}
                        for revision_idx in range(self._reflection_config.max_revisions + 1):
                            critique = await self._critique_answer(trajectory, candidate_answer)

                            self._emit_event(
                                PlannerEvent(
                                    event_type="reflection_critique",
                                    ts=self._time_source(),
                                    trajectory_step=len(trajectory.steps),
                                    thought=action.thought,
                                    extra={
                                        "score": critique.score,
                                        "passed": critique.passed,
                                        "revision": revision_idx,
                                        "feedback": critique.feedback[:200],
                                    },
                                )
                            )

                            if critique.passed or critique.score >= self._reflection_config.quality_threshold:
                                logger.info(
                                    "reflection_passed",
                                    extra={
                                        "score": critique.score,
                                        "revisions": revision_idx,
                                    },
                                )
                                break

                            if revision_idx >= self._reflection_config.max_revisions:
                                threshold = self._reflection_config.quality_threshold

                                # Check if quality is still below threshold
                                if critique.score < threshold:
                                    # Quality remains poor - transform into honest clarification
                                    logger.warning(
                                        "reflection_honest_failure",
                                        extra={
                                            "score": critique.score,
                                            "threshold": threshold,
                                            "revisions": revision_idx,
                                        },
                                    )

                                    # Generate clarification instead of returning low-quality answer
                                    clarification_text = await self._generate_clarification(
                                        trajectory=trajectory,
                                        failed_answer=candidate_answer,
                                        critique=critique,
                                        revision_attempts=revision_idx,
                                    )

                                    # Replace candidate answer with clarification
                                    # Ensure proper structure for downstream consumers (like FinalAnswer model)
                                    if isinstance(candidate_answer, dict):
                                        # Update existing dict with clarification
                                        candidate_answer["raw_answer"] = clarification_text
                                        candidate_answer["text"] = clarification_text

                                        # Ensure required fields are present
                                        if "route" not in candidate_answer:
                                            # Extract route from first step observation if available
                                            route = "unknown"
                                            if trajectory.steps and trajectory.steps[0].observation:
                                                obs = trajectory.steps[0].observation
                                                # Handle both dict and Pydantic model observations
                                                if isinstance(obs, dict):
                                                    route = obs.get("route", "unknown")
                                                else:
                                                    route = getattr(obs, "route", "unknown")
                                            candidate_answer["route"] = route
                                        if "artifacts" not in candidate_answer:
                                            candidate_answer["artifacts"] = {}
                                        if "metadata" not in candidate_answer:
                                            candidate_answer["metadata"] = {}

                                        # Mark as unsatisfied in metadata
                                        candidate_answer["metadata"]["confidence"] = "unsatisfied"
                                        candidate_answer["metadata"]["reflection_score"] = critique.score
                                        candidate_answer["metadata"]["revision_attempts"] = revision_idx
                                    else:
                                        # Create structured answer from scratch
                                        route = "unknown"
                                        if trajectory.steps and trajectory.steps[0].observation:
                                            obs = trajectory.steps[0].observation
                                            # Handle both dict and Pydantic model observations
                                            if isinstance(obs, dict):
                                                route = obs.get("route", "unknown")
                                            else:
                                                route = getattr(obs, "route", "unknown")

                                        candidate_answer = {
                                            "raw_answer": clarification_text,
                                            "text": clarification_text,
                                            "route": route,
                                            "artifacts": {},
                                            "metadata": {
                                                "confidence": "unsatisfied",
                                                "reflection_score": critique.score,
                                                "revision_attempts": revision_idx,
                                            },
                                        }

                                    # Emit telemetry event
                                    self._emit_event(
                                        PlannerEvent(
                                            event_type="reflection_clarification_generated",
                                            ts=self._time_source(),
                                            trajectory_step=len(trajectory.steps),
                                            thought="Generated clarification for unsatisfiable query",
                                            extra={
                                                "original_score": critique.score,
                                                "threshold": threshold,
                                                "revisions": revision_idx,
                                            },
                                        )
                                    )
                                else:
                                    # Quality improved enough, just log warning
                                    logger.warning(
                                        "reflection_max_revisions",
                                        extra={
                                            "score": critique.score,
                                            "threshold": threshold,
                                        },
                                    )

                                break

                            if not tracker.has_budget_for_next_tool():
                                snapshot = tracker.snapshot()
                                logger.warning(
                                    "reflection_budget_exhausted",
                                    extra={
                                        "score": critique.score,
                                        "hops_used": snapshot.get("hops_used"),
                                    },
                                )
                                break

                            logger.debug(
                                "reflection_requesting_revision",
                                extra={
                                    "revision": revision_idx + 1,
                                    "score": critique.score,
                                },
                            )

                            # Build streaming callback for revision (reuse extractor pattern)
                            revision_extractor = _StreamingArgsExtractor()

                            def _emit_revision_chunk(
                                text: str,
                                done: bool,
                                *,
                                _extractor: _StreamingArgsExtractor = revision_extractor,
                                _revision_idx: int = revision_idx,
                            ) -> None:
                                if self._event_callback is None:
                                    return

                                args_chars = _extractor.feed(text)

                                if args_chars:
                                    args_text = "".join(args_chars)
                                    self._emit_event(
                                        PlannerEvent(
                                            event_type="llm_stream_chunk",
                                            ts=self._time_source(),
                                            trajectory_step=len(trajectory.steps),
                                            extra={
                                                "text": args_text,
                                                "done": False,
                                                "phase": "revision",
                                                "revision_idx": _revision_idx + 1,
                                            },
                                        )
                                    )

                                if done and _extractor.is_finish_action:
                                    self._emit_event(
                                        PlannerEvent(
                                            event_type="llm_stream_chunk",
                                            ts=self._time_source(),
                                            trajectory_step=len(trajectory.steps),
                                            extra={
                                                "text": "",
                                                "done": True,
                                                "phase": "revision",
                                                "revision_idx": _revision_idx + 1,
                                            },
                                        )
                                    )

                            revision_action = await self._request_revision(
                                trajectory,
                                critique,
                                on_stream_chunk=_emit_revision_chunk if self._stream_final_response else None,
                            )
                            candidate_answer = revision_action.args or revision_action.model_dump()
                            trajectory.steps.append(
                                TrajectoryStep(
                                    action=revision_action,
                                    observation={"status": "revision_requested"},
                                )
                            )
                            trajectory.summary = None

                        if critique is not None:
                            metadata_reflection = {
                                "score": critique.score,
                                "revisions": min(
                                    revision_idx,
                                    self._reflection_config.max_revisions,
                                ),
                                "passed": critique.passed,
                            }
                            if critique.feedback:
                                metadata_reflection["feedback"] = critique.feedback

                    metadata_extra: dict[str, Any] | None = None
                    if metadata_reflection is not None:
                        metadata_extra = {"reflection": metadata_reflection}

                    trajectory.artifacts = artifact_collector.snapshot()
                    trajectory.sources = source_collector.snapshot()
                    final_payload = self._build_final_payload(
                        candidate_answer,
                        last_observation,
                        trajectory.artifacts,
                        trajectory.sources,
                    )
                    # Note: Real-time streaming of args content happens during LLM call
                    # via _StreamingArgsExtractor in step(). No post-hoc chunking needed.

                    return self._finish(
                        trajectory,
                        reason="answer_complete",
                        payload=final_payload.model_dump(mode="json"),
                        thought=action.thought,
                        constraints=tracker,
                        metadata_extra=metadata_extra,
                    )

                spec = self._spec_by_name.get(action.next_node)
                if spec is None:
                    error = prompts.render_invalid_node(
                        action.next_node,
                        list(self._spec_by_name.keys()),
                    )
                    trajectory.steps.append(TrajectoryStep(action=action, error=error))
                    trajectory.summary = None
                    continue

                try:
                    parsed_args = spec.args_model.model_validate(action.args or {})
                except ValidationError as exc:
                    autofilled = _autofill_missing_args(spec, action.args)
                    if autofilled is not None:
                        autofilled_args, filled_fields = autofilled
                        try:
                            parsed_args = spec.args_model.model_validate(autofilled_args)
                            action.args = autofilled_args
                            logger.info(
                                "planner_autofill_args",
                                extra={
                                    "tool": spec.name,
                                    "filled": list(filled_fields),
                                },
                            )
                        except ValidationError as autofill_exc:
                            error = prompts.render_validation_error(
                                spec.name,
                                json.dumps(autofill_exc.errors(), ensure_ascii=False),
                            )
                            trajectory.steps.append(TrajectoryStep(action=action, error=error))
                            trajectory.summary = None
                            continue
                    else:
                        error = prompts.render_validation_error(
                            spec.name,
                            json.dumps(exc.errors(), ensure_ascii=False),
                        )
                        trajectory.steps.append(TrajectoryStep(action=action, error=error))
                        trajectory.summary = None
                        continue

                ctx = _PlannerContext(self, trajectory)
                try:
                    result = await spec.node.func(parsed_args, ctx)
                except _PlannerPauseSignal as signal:
                    tracker.record_hop()
                    pause_chunks = ctx._collect_chunks()
                    trajectory.steps.append(
                        TrajectoryStep(
                            action=action,
                            observation={
                                "pause": signal.pause.reason,
                                "payload": signal.pause.payload,
                            },
                            streams=pause_chunks or None,
                        )
                    )
                    trajectory.summary = None
                    await self._record_pause(signal.pause, trajectory, tracker)
                    return signal.pause
                except Exception as exc:
                    failure_payload = self._build_failure_payload(spec, parsed_args, exc)
                    error = f"tool '{spec.name}' raised {exc.__class__.__name__}: {exc}"
                    failure_chunks = ctx._collect_chunks()
                    trajectory.steps.append(
                        TrajectoryStep(
                            action=action,
                            error=error,
                            failure=failure_payload,
                            streams=failure_chunks or None,
                        )
                    )
                    tracker.record_hop()
                    trajectory.summary = None
                    last_observation = None
                    continue

                step_chunks = ctx._collect_chunks()

                try:
                    observation = spec.out_model.model_validate(result)
                except ValidationError as exc:
                    error = prompts.render_output_validation_error(
                        spec.name,
                        json.dumps(exc.errors(), ensure_ascii=False),
                    )
                    tracker.record_hop()
                    trajectory.steps.append(
                        TrajectoryStep(
                            action=action,
                            error=error,
                            streams=step_chunks or None,
                        )
                    )
                    trajectory.summary = None
                    last_observation = None
                    continue

                observation_json = observation.model_dump(mode="json")

                artifact_collector.collect(spec.name, spec.out_model, observation_json)
                source_collector.collect(spec.out_model, observation_json)

                trajectory.steps.append(
                    TrajectoryStep(
                        action=action,
                        observation=observation_json,
                        llm_observation=_redact_artifacts(spec.out_model, observation_json),
                        streams=step_chunks or None,
                    )
                )
                tracker.record_hop()
                trajectory.summary = None
                last_observation = observation_json
                trajectory.artifacts = artifact_collector.snapshot()
                trajectory.sources = source_collector.snapshot()
                self._record_hint_progress(spec.name, trajectory)
                trajectory.resume_user_input = None

                # Emit step complete event
                step_latency = (self._time_source() - step_start_ts) * 1000  # ms
                self._emit_event(
                    PlannerEvent(
                        event_type="step_complete",
                        ts=self._time_source(),
                        trajectory_step=len(trajectory.steps) - 1,
                        thought=action.thought,
                        node_name=spec.name,
                        latency_ms=step_latency,
                    )
                )

            if tracker.deadline_triggered or tracker.hop_exhausted:
                thought = (
                    prompts.render_deadline_exhausted()
                    if tracker.deadline_triggered
                    else prompts.render_hop_budget_violation(self._hop_budget or 0)
                )
                trajectory.artifacts = artifact_collector.snapshot()
                trajectory.sources = source_collector.snapshot()
                return self._finish(
                    trajectory,
                    reason="budget_exhausted",
                    payload=last_observation,
                    thought=thought,
                    constraints=tracker,
                )
            trajectory.artifacts = artifact_collector.snapshot()
            trajectory.sources = source_collector.snapshot()
            return self._finish(
                trajectory,
                reason="no_path",
                payload=last_observation,
                thought="iteration limit reached",
                constraints=tracker,
            )
        finally:
            self._active_trajectory = None
            self._active_tracker = None

    async def step(self, trajectory: Trajectory) -> PlannerAction:
        base_messages = await self._build_messages(trajectory)
        messages: list[dict[str, str]] = list(base_messages)
        last_error: str | None = None
        last_raw: str | None = None

        for _ in range(self._repair_attempts):
            if last_error is not None:
                messages = list(base_messages) + [
                    {
                        "role": "system",
                        "content": prompts.render_repair_message(last_error),
                    }
                ]

            response_format: Mapping[str, Any] | None = self._response_format
            if response_format is None and getattr(self._client, "expects_json_schema", False):
                response_format = self._action_schema

            stream_allowed = (
                self._stream_final_response
                and isinstance(self._client, _LiteLLMJSONClient)
                and (
                    response_format is None
                    or (
                        isinstance(response_format, Mapping)
                        and response_format.get("type") in ("json_object", "json_schema")
                    )
                )
            )

            # Create extractor to detect finish actions and stream args content
            args_extractor = _StreamingArgsExtractor()
            thought_extractor = _StreamingThoughtExtractor()

            current_action_seq = self._action_seq

            def _emit_llm_chunk(
                text: str,
                done: bool,
                *,
                _extractor: _StreamingArgsExtractor = args_extractor,
                _thought_extractor: _StreamingThoughtExtractor = thought_extractor,
                _action_seq: int = current_action_seq,
            ) -> None:
                if self._event_callback is None:
                    return

                # DEBUG: Log incoming chunks
                logger.debug(
                    "llm_chunk_received",
                    extra={"text": text[:100] if text else "", "done": done, "buffer_len": len(_extractor._buffer)},
                )

                thought_chars = _thought_extractor.feed(text)
                if thought_chars:
                    thought_text = "".join(thought_chars)
                    self._emit_event(
                        PlannerEvent(
                            event_type="llm_stream_chunk",
                            ts=self._time_source(),
                            trajectory_step=len(trajectory.steps),
                            extra={
                                "text": thought_text,
                                "done": False,
                                "phase": "observation",
                                "channel": "thinking",
                            },
                        )
                    )

                # Feed chunk to extractor to detect args content
                args_chars = _extractor.feed(text)

                # DEBUG: Log extractor state
                logger.debug(
                    "extractor_state",
                    extra={
                        "is_finish": _extractor.is_finish_action,
                        "in_args_string": _extractor._in_args_string,
                        "chars_extracted": len(args_chars),
                    },
                )

                # Emit args content as "answer" phase for real-time display
                if args_chars:
                    # Batch small chars into reasonable chunks for efficiency
                    args_text = "".join(args_chars)
                    self._emit_event(
                        PlannerEvent(
                            event_type="llm_stream_chunk",
                            ts=self._time_source(),
                            trajectory_step=len(trajectory.steps),
                            extra={
                                "text": args_text,
                                "done": False,
                                "phase": "answer",
                                "channel": "answer",
                                "action_seq": _action_seq,
                            },
                        )
                    )

                # Emit done signal when LLM finishes and it was a finish action
                if done and _extractor.is_finish_action:
                    self._emit_event(
                        PlannerEvent(
                            event_type="llm_stream_chunk",
                            ts=self._time_source(),
                            trajectory_step=len(trajectory.steps),
                            extra={
                                "text": "",
                                "done": True,
                                "phase": "answer",
                                "channel": "answer",
                                "action_seq": _action_seq,
                            },
                        )
                    )

            if self._event_callback is not None:
                self._emit_event(
                    PlannerEvent(
                        event_type="llm_stream_chunk",
                        ts=self._time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={
                            "text": "",
                            "done": False,
                            "phase": "action",
                            "channel": "thinking",
                            "action_seq": current_action_seq,
                        },
                    )
                )
            try:
                llm_result = await self._client.complete(
                    messages=messages,
                    response_format=response_format,
                    stream=stream_allowed,
                    on_stream_chunk=_emit_llm_chunk if stream_allowed else None,
                )
            finally:
                if self._event_callback is not None:
                    self._emit_event(
                        PlannerEvent(
                            event_type="llm_stream_chunk",
                            ts=self._time_source(),
                            trajectory_step=len(trajectory.steps),
                            extra={"text": "", "done": True, "phase": "action", "channel": "thinking"},
                        )
                    )
            raw, cost = _coerce_llm_response(llm_result)
            last_raw = raw
            self._cost_tracker.record_main_call(cost)

            try:
                return PlannerAction.model_validate_json(raw)
            except ValidationError as exc:
                salvaged = _salvage_action_payload(raw)
                if salvaged is not None:
                    logger.info(
                        "planner_action_salvaged",
                        extra={"errors": json.dumps(exc.errors(), ensure_ascii=False)},
                    )
                    return salvaged
                last_error = json.dumps(exc.errors(), ensure_ascii=False)
                continue

        if last_raw is not None:
            # Try to extract raw_answer/answer content using regex before naive truncation
            # This handles cases where the JSON is malformed but raw_answer is readable
            extracted_answer: str | None = None
            import re

            # Look for "raw_answer": "..." or "answer": "..." pattern
            answer_match = re.search(
                r'"(?:raw_answer|answer)"\s*:\s*"((?:[^"\\]|\\.)*)',
                last_raw,
                re.DOTALL,
            )
            if answer_match:
                extracted_answer = answer_match.group(1)
                # Unescape common JSON escapes
                extracted_answer = (
                    extracted_answer.replace("\\n", "\n")
                    .replace("\\t", "\t")
                    .replace('\\"', '"')
                    .replace("\\\\", "\\")
                )
                # Clean up trailing JSON artifacts that might be present
                # (e.g., if the string was cut off or malformed)
                extracted_answer = re.sub(r'["\}\]]+\s*$', "", extracted_answer).strip()
                logger.info(
                    "planner_fallback_answer_extracted",
                    extra={"length": len(extracted_answer)},
                )

            fallback_answer = extracted_answer if extracted_answer else last_raw.strip()[:2000]
            fallback = PlannerAction(
                thought="fallback finish after repair failures",
                next_node=None,
                args={"raw_answer": fallback_answer},
                plan=None,
                join=None,
            )
            logger.warning(
                "planner_fallback_finish",
                extra={
                    "reason": "repair_exhausted",
                    "extraction_method": "regex" if extracted_answer else "truncation",
                },
            )
            return fallback

        raise RuntimeError("Planner failed to produce valid JSON after repair attempts")

    async def _execute_parallel_plan(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
        artifact_collector: _ArtifactCollector,
        source_collector: _SourceCollector,
    ) -> tuple[Any | None, PlannerPause | None]:
        return await execute_parallel_plan(
            self,
            action,
            trajectory,
            tracker,
            artifact_collector,
            source_collector,
        )

    def _make_context(self, trajectory: Trajectory) -> _PlannerContext:
        return _PlannerContext(self, trajectory)

    async def _build_messages(self, trajectory: Trajectory) -> list[dict[str, str]]:
        return await build_messages(self, trajectory)

    def _estimate_size(self, messages: Sequence[Mapping[str, str]]) -> int:
        return _estimate_size(messages)

    async def _summarise_trajectory(self, trajectory: Trajectory) -> TrajectorySummary:
        return await summarise_trajectory(self, trajectory)

    async def _critique_answer(
        self,
        trajectory: Trajectory,
        candidate: Any,
    ) -> ReflectionCritique:
        return await critique_answer(self, trajectory, candidate)

    async def _request_revision(
        self,
        trajectory: Trajectory,
        critique: ReflectionCritique,
        *,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> PlannerAction:
        return await request_revision(self, trajectory, critique, on_stream_chunk=on_stream_chunk)

    async def _generate_clarification(
        self,
        trajectory: Trajectory,
        failed_answer: str | dict[str, Any] | Any,
        critique: ReflectionCritique,
        revision_attempts: int,
    ) -> str:
        return await generate_clarification(self, trajectory, failed_answer, critique, revision_attempts)

    def _check_action_constraints(
        self,
        action: PlannerAction,
        trajectory: Trajectory,
        tracker: _ConstraintTracker,
    ) -> str | None:
        hints = self._planning_hints
        node_name = action.next_node
        if node_name and not tracker.has_budget_for_next_tool():
            limit = self._hop_budget if self._hop_budget is not None else 0
            return prompts.render_hop_budget_violation(limit)
        if node_name and node_name in hints.disallow_nodes:
            return prompts.render_disallowed_node(node_name)

        # Check parallel execution limits
        if action.plan:
            # Absolute system-level safety limit
            if len(action.plan) > self._absolute_max_parallel:
                logger.warning(
                    "parallel_limit_absolute",
                    extra={
                        "requested": len(action.plan),
                        "limit": self._absolute_max_parallel,
                    },
                )
                return prompts.render_parallel_limit(self._absolute_max_parallel)
            # Hint-based limit
            if hints.max_parallel is not None and len(action.plan) > hints.max_parallel:
                return prompts.render_parallel_limit(hints.max_parallel)
        if hints.sequential_only and action.plan:
            for item in action.plan:
                candidate = item.node
                if candidate in hints.sequential_only:
                    return prompts.render_sequential_only(candidate)
        if hints.ordering_hints and node_name is not None:
            state = trajectory.hint_state.setdefault(
                "ordering_state",
                {"completed": [], "warned": False},
            )
            completed = state.setdefault("completed", [])
            expected_index = len(completed)
            if expected_index < len(hints.ordering_hints):
                expected_node = hints.ordering_hints[expected_index]
                if node_name != expected_node:
                    if node_name in hints.ordering_hints and not state.get("warned", False):
                        state["warned"] = True
                        return prompts.render_ordering_hint_violation(
                            hints.ordering_hints,
                            node_name,
                        )
        return None

    def _record_hint_progress(self, node_name: str, trajectory: Trajectory) -> None:
        hints = self._planning_hints
        if not hints.ordering_hints:
            return
        state = trajectory.hint_state.setdefault(
            "ordering_state",
            {"completed": [], "warned": False},
        )
        completed = state.setdefault("completed", [])
        expected_index = len(completed)
        if expected_index < len(hints.ordering_hints) and node_name == hints.ordering_hints[expected_index]:
            completed.append(node_name)
            state["warned"] = False

    def _build_failure_payload(self, spec: NodeSpec, args: BaseModel, exc: Exception) -> dict[str, Any]:
        suggestion = getattr(exc, "suggestion", None)
        if suggestion is None:
            suggestion = getattr(exc, "remedy", None)
        payload: dict[str, Any] = {
            "node": spec.name,
            "args": args.model_dump(mode="json"),
            "error_code": exc.__class__.__name__,
            "message": str(exc),
        }
        if suggestion:
            payload["suggestion"] = str(suggestion)
        return payload

    def _build_final_payload(
        self,
        args: Mapping[str, Any] | Any | None,
        last_observation: Any,
        artifacts: Mapping[str, Any],
        sources: Sequence[Mapping[str, Any]] | None,
    ) -> FinalPayload:
        logger.debug(
            "build_final_payload_start",
            extra={
                "args_type": type(args).__name__,
                "args_value": str(args)[:500] if args else None,
                "last_observation_type": type(last_observation).__name__ if last_observation else None,
                "last_observation_value": str(last_observation)[:500] if last_observation else None,
            },
        )

        payload_data: dict[str, Any] = {}
        if isinstance(args, BaseModel):
            payload_data.update(args.model_dump(mode="json"))
        elif isinstance(args, Mapping):
            payload_data.update(args)
        elif args is not None:
            payload_data["raw_answer"] = _fallback_answer(args)

        if not payload_data.get("raw_answer"):
            # Try args first (the LLM's answer), then last_observation
            for source in (args, last_observation):
                if source is not None:
                    extracted = _fallback_answer(source)
                    logger.debug(
                        "fallback_answer_extraction",
                        extra={
                            "source_type": type(source).__name__,
                            "extracted": extracted[:200] if extracted else None,
                        },
                    )
                    if extracted and extracted != "No answer produced.":
                        payload_data["raw_answer"] = extracted
                        break
            else:
                logger.warning(
                    "no_answer_extracted",
                    extra={
                        "input_args": str(args)[:200] if args else None,
                        "last_observation": str(last_observation)[:200] if last_observation else None,
                    },
                )
                payload_data["raw_answer"] = "No answer produced."

        payload_data["artifacts"] = dict(artifacts)
        if sources is not None:
            payload_data["sources"] = list(sources)

        known_fields = set(FinalPayload.model_fields)
        extra_payload: dict[str, Any] = {}
        existing_extra = payload_data.get("extra")
        if isinstance(existing_extra, Mapping):
            extra_payload.update(existing_extra)

        for key in list(payload_data.keys()):
            if key not in known_fields:
                extra_payload[key] = payload_data.pop(key)

        if extra_payload:
            payload_data["extra"] = extra_payload

        try:
            return FinalPayload.model_validate(payload_data)
        except ValidationError as exc:
            logger.warning(
                "final_payload_validation_failed",
                extra={"error": str(exc)},
            )
            # Try args first, then last_observation (consistent with above)
            raw_answer = "No answer produced."
            for source in (args, last_observation):
                if source is not None:
                    extracted = _fallback_answer(source)
                    if extracted and extracted != "No answer produced.":
                        raw_answer = extracted
                        break
            return FinalPayload(
                raw_answer=raw_answer,
                artifacts=dict(artifacts),
            )

    async def pause(self, reason: PlannerPauseReason, payload: Mapping[str, Any] | None = None) -> PlannerPause:
        if self._active_trajectory is None:
            raise RuntimeError("pause() requires an active planner run")
        try:
            await self._pause_from_context(
                reason,
                dict(payload or {}),
                self._active_trajectory,
            )
        except _PlannerPauseSignal as signal:
            return signal.pause
        raise RuntimeError("pause request did not trigger")

    async def _pause_from_context(
        self,
        reason: PlannerPauseReason,
        payload: dict[str, Any],
        trajectory: Trajectory,
    ) -> PlannerPause:
        if not self._pause_enabled:
            raise RuntimeError("Pause/resume is disabled for this planner")
        pause = PlannerPause(
            reason=reason,
            payload=dict(payload),
            resume_token=uuid4().hex,
        )
        await self._record_pause(pause, trajectory, self._active_tracker)
        raise _PlannerPauseSignal(pause)

    async def _record_pause(
        self,
        pause: PlannerPause,
        trajectory: Trajectory,
        tracker: _ConstraintTracker | None,
    ) -> None:
        snapshot = Trajectory.from_serialised(trajectory.serialise())
        snapshot.tool_context = dict(trajectory.tool_context or {})
        record = _PauseRecord(
            trajectory=snapshot,
            reason=pause.reason,
            payload=dict(pause.payload),
            constraints=tracker.snapshot() if tracker is not None else None,
            tool_context=dict(snapshot.tool_context or {}),
        )
        await self._store_pause_record(pause.resume_token, record)

    async def _store_pause_record(self, token: str, record: _PauseRecord) -> None:
        self._pause_records[token] = record
        if self._state_store is None:
            return
        saver = getattr(self._state_store, "save_planner_state", None)
        if saver is None:
            logger.debug(
                "state_store_no_save_method",
                extra={"token": token[:8] + "..."},
            )
            return

        try:
            payload = self._serialise_pause_record(record)
            result = saver(token, payload)
            if inspect.isawaitable(result):
                await result
            logger.debug("pause_record_saved", extra={"token": token[:8] + "..."})
        except Exception as exc:
            # Log error but don't fail the pause operation
            # In-memory fallback already succeeded
            logger.error(
                "state_store_save_failed",
                extra={
                    "token": token[:8] + "...",
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )

    async def _load_pause_record(self, token: str) -> _PauseRecord:
        record = self._pause_records.pop(token, None)
        if record is not None:
            logger.debug("pause_record_loaded", extra={"source": "memory"})
            return record

        if self._state_store is not None:
            loader = getattr(self._state_store, "load_planner_state", None)
            if loader is not None:
                try:
                    result = loader(token)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is None:
                        raise KeyError(token)
                    trajectory = Trajectory.from_serialised(result["trajectory"])
                    payload = dict(result.get("payload", {}))
                    reason = result.get("reason", "await_input")
                    constraints = result.get("constraints")
                    tool_context_payload = result.get("tool_context")
                    tool_context = dict(tool_context_payload) if isinstance(tool_context_payload, Mapping) else None
                    logger.debug("pause_record_loaded", extra={"source": "state_store"})
                    return _PauseRecord(
                        trajectory=trajectory,
                        reason=reason,
                        payload=payload,
                        constraints=constraints,
                        tool_context=tool_context,
                    )
                except KeyError:
                    raise
                except Exception as exc:
                    # Log error and re-raise as KeyError with context
                    logger.error(
                        "state_store_load_failed",
                        extra={
                            "token": token[:8] + "...",
                            "error": str(exc),
                            "error_type": exc.__class__.__name__,
                        },
                    )
                    raise KeyError(f"Failed to load pause record: {exc}") from exc

        raise KeyError(token)

    def _serialise_pause_record(self, record: _PauseRecord) -> dict[str, Any]:
        tool_context: dict[str, Any] | None = None
        if record.tool_context is not None:
            try:
                tool_context = json.loads(json.dumps(record.tool_context, ensure_ascii=False))
            except (TypeError, ValueError):
                tool_context = None
        return {
            "trajectory": record.trajectory.serialise(),
            "reason": record.reason,
            "payload": dict(record.payload),
            "constraints": dict(record.constraints) if record.constraints is not None else None,
            "tool_context": tool_context,
        }

    def _emit_event(self, event: PlannerEvent) -> None:
        """Emit a planner event for observability."""
        # Log the event (strip reserved logging keys to avoid collisions)
        payload = event.to_payload()
        for reserved in ("args", "msg", "levelname", "levelno", "exc_info"):
            payload.pop(reserved, None)
        logger.info(event.event_type, extra=payload)

        # Invoke callback if provided
        if self._event_callback is not None:
            try:
                self._event_callback(event)
            except Exception:
                logger.exception(
                    "event_callback_error",
                    extra={
                        "event_type": event.event_type,
                        "step": event.trajectory_step,
                    },
                )
        self._last_event = event

    def _finish(
        self,
        trajectory: Trajectory,
        *,
        reason: Literal["answer_complete", "no_path", "budget_exhausted"],
        payload: Any,
        thought: str,
        constraints: _ConstraintTracker | None = None,
        error: str | None = None,
        metadata_extra: Mapping[str, Any] | None = None,
    ) -> PlannerFinish:
        metadata = {
            "reason": reason,
            "thought": thought,
            "steps": trajectory.to_history(),
            "step_count": len(trajectory.steps),
            "artifacts": dict(trajectory.artifacts),
            "sources": list(trajectory.sources),
        }
        metadata["cost"] = self._cost_tracker.snapshot()
        if constraints is not None:
            metadata["constraints"] = constraints.snapshot()
        if error is not None:
            metadata["error"] = error
        if metadata_extra:
            metadata.update(metadata_extra)

        # Emit finish event
        extra_data: dict[str, Any] = {
            "reason": reason,
            "cost": metadata["cost"],
            "answer_action_seq": self._action_seq,
        }
        if error:
            extra_data["error"] = error
        self._emit_event(
            PlannerEvent(
                event_type="finish",
                ts=self._time_source(),
                trajectory_step=len(trajectory.steps),
                thought=thought,
                extra=extra_data,
            )
        )

        logger.info(
            "planner_finish",
            extra={
                "reason": reason,
                "step_count": len(trajectory.steps),
                "thought": thought,
            },
        )

        return PlannerFinish(reason=reason, payload=payload, metadata=metadata)


__all__ = [
    "JoinInjection",
    "ParallelCall",
    "ParallelJoin",
    "PlannerAction",
    "PlannerEvent",
    "PlannerEventCallback",
    "PlannerFinish",
    "PlannerPause",
    "ReflectionConfig",
    "ReflectionCriteria",
    "ReflectionCritique",
    "FinalPayload",
    "ReactPlanner",
    "_sanitize_json_schema",
    "Trajectory",
    "TrajectoryStep",
    "TrajectorySummary",
]
