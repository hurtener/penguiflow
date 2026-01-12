"""Short-term memory integration helpers for the React planner."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from . import prompts
from .llm import _coerce_llm_response, _sanitize_json_schema
from .memory import (
    ConversationTurn,
    DefaultShortTermMemory,
    MemoryKey,
    ShortTermMemory,
    TrajectoryDigest,
)
from .models import PlannerFinish, PlannerPause
from .trajectory import Trajectory

logger = logging.getLogger("penguiflow.planner")

_STM_SUMMARY_SCHEMA_NAME = "short_term_memory_summary"


class _ShortTermMemorySummary(BaseModel):
    summary: str


def _resolve_memory_key(
    planner: Any,
    explicit: MemoryKey | None,
    tool_context: Mapping[str, Any] | None,
) -> MemoryKey | None:
    if planner._memory_singleton is None and planner._memory_config.strategy == "none":
        return None
    if explicit is not None:
        return explicit
    extracted = _extract_memory_key_from_tool_context(planner, tool_context or {})
    if extracted is not None:
        return extracted
    if planner._memory_config.isolation.require_explicit_key:
        return None
    if planner._memory_ephemeral_key is None:
        planner._memory_ephemeral_key = MemoryKey(
            tenant_id="default",
            user_id="anonymous",
            session_id=uuid4().hex,
        )
    return planner._memory_ephemeral_key


def _get_memory_for_key(planner: Any, key: MemoryKey) -> ShortTermMemory | None:
    if planner._memory_singleton is not None:
        return planner._memory_singleton
    if planner._memory_config.strategy == "none":
        return None
    composite = key.composite()
    memory = planner._memory_by_key.get(composite)
    if memory is None:
        summarizer = None
        if planner._memory_config.strategy == "rolling_summary":
            summarizer = _get_short_term_memory_summarizer(planner)
        memory = DefaultShortTermMemory(config=planner._memory_config, summarizer=summarizer)
        planner._memory_by_key[composite] = memory
    return memory


def _normalise_session_summary(summary: str) -> str:
    summary = summary.strip()
    if not summary:
        return "<session_summary></session_summary>"
    if "<session_summary>" not in summary:
        return f"<session_summary>\n{summary}\n</session_summary>"
    return summary


def _get_short_term_memory_summarizer(
    planner: Any,
) -> Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]:
    if planner._memory_summarizer is not None:
        return planner._memory_summarizer

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
                "has_dedicated_client": planner._memory_summarizer_client is not None,
            },
        )

        client = planner._memory_summarizer_client or planner._client
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
        summary = _normalise_session_summary(parsed.summary)

        logger.debug(
            "memory_summarizer_call_complete",
            extra={
                "summary_len": len(summary),
                "turns_processed": len(turns),
            },
        )

        return {"summary": summary}

    planner._memory_summarizer = _summarize
    return planner._memory_summarizer


def _extract_path(mapping: Mapping[str, Any], path: str) -> Any | None:
    current: Any = mapping
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        if part not in current:
            return None
        current = current[part]
    return current


def _extract_memory_key_from_tool_context(planner: Any, tool_context: Mapping[str, Any]) -> MemoryKey | None:
    isolation = planner._memory_config.isolation
    tenant_value = _extract_path(tool_context, isolation.tenant_key)
    user_value = _extract_path(tool_context, isolation.user_key)
    session_value = _extract_path(tool_context, isolation.session_key)
    if session_value is None or str(session_value).strip() == "":
        return None
    tenant_id = str(tenant_value).strip() if tenant_value is not None else "default"
    user_id = str(user_value).strip() if user_value is not None else "anonymous"
    return MemoryKey(tenant_id=tenant_id, user_id=user_id, session_id=str(session_value).strip())


async def _apply_memory_context(
    planner: Any,
    llm_context: dict[str, Any] | None,
    key: MemoryKey | None,
) -> dict[str, Any] | None:
    if key is None:
        return llm_context
    memory = _get_memory_for_key(planner, key)
    if memory is None:
        return llm_context
    await _maybe_memory_hydrate(planner, memory, key)
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


async def _maybe_memory_hydrate(planner: Any, memory: ShortTermMemory, key: MemoryKey) -> None:
    if planner._state_store is None:
        return
    hydrate = getattr(memory, "hydrate", None)
    if hydrate is None:
        return
    try:
        await hydrate(planner._state_store, key.composite())
    except Exception as exc:
        logger.warning(
            "memory_hydrate_failed",
            extra={"error": str(exc), "error_type": exc.__class__.__name__},
        )


async def _maybe_memory_persist(planner: Any, memory: ShortTermMemory, key: MemoryKey) -> None:
    if planner._state_store is None:
        return
    persist = getattr(memory, "persist", None)
    if persist is None:
        return
    try:
        await persist(planner._state_store, key.composite())
    except Exception as exc:
        logger.warning(
            "memory_persist_failed",
            extra={"error": str(exc), "error_type": exc.__class__.__name__},
        )


def _build_memory_turn(
    planner: Any,
    query: str,
    result: PlannerFinish,
    trajectory: Trajectory,
) -> ConversationTurn:
    payload = result.payload
    if isinstance(payload, Mapping):
        assistant = payload.get("raw_answer")
        assistant_response = assistant if isinstance(assistant, str) else json.dumps(payload, ensure_ascii=False)
    else:
        assistant_response = str(payload) if payload is not None else ""

    digest: TrajectoryDigest | None = None
    if planner._memory_config.include_trajectory_digest:
        tools: list[str] = []
        obs_lines: list[str] = []
        for step in trajectory.steps:
            if not step.action.is_tool_call():
                continue
            tool_name = step.action.next_node
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
    planner: Any,
    query: str,
    result: PlannerFinish | PlannerPause,
    trajectory: Trajectory,
    key: MemoryKey | None,
) -> None:
    if key is None:
        return
    memory = _get_memory_for_key(planner, key)
    if memory is None:
        return
    if not isinstance(result, PlannerFinish):
        return
    turn = _build_memory_turn(planner, query, result, trajectory)
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
    await _maybe_memory_persist(planner, memory, key)
