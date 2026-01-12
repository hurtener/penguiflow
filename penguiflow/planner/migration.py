"""Planner action normalization helpers.

Phase 2 (RFC_UNIFIED_ACTION_SCHEMA): accept legacy/unified/hybrid action payloads and
normalize them into the unified internal ``PlannerAction`` model shape:

- ``next_node`` is always a non-null string
- ``args`` is always an object (defaults to {})

This is intentionally best-effort and is primarily a weak-model robustness feature.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from .models import PlannerAction

_DEFAULT_THOUGHT = "planning next step"

def dump_action_legacy(action: PlannerAction) -> dict[str, Any]:
    """Render a ``PlannerAction`` as the legacy on-disk/metadata shape.

    This keeps existing internal surfaces stable (trajectory metadata, pause records),
    while runtime and response_format move to the unified action schema.
    """

    thought = action.thought or _DEFAULT_THOUGHT

    if action.next_node == "parallel":
        steps = action.args.get("steps")
        join = action.args.get("join")
        return {
            "thought": thought,
            "next_node": None,
            "args": None,
            "plan": list(steps) if isinstance(steps, list) else [],
            "join": dict(join) if isinstance(join, Mapping) else None,
        }

    if action.next_node == "final_response":
        args = dict(action.args or {})
        answer = action.answer_text() or ""
        args.setdefault("raw_answer", answer)
        return {
            "thought": thought,
            "next_node": None,
            "args": args,
            "plan": None,
            "join": None,
        }

    return {
        "thought": thought,
        "next_node": action.next_node,
        "args": dict(action.args or {}),
        "plan": None,
        "join": None,
    }


def normalize_action(raw: str | Mapping[str, Any]) -> PlannerAction:
    """Normalize a legacy/unified/hybrid action payload into ``PlannerAction``.

    Notes:
    - Legacy fields (``thought`` / ``plan`` / ``join`` / ``next_node: null``) are supported as input
      and converted into unified opcodes (``final_response`` / ``parallel``).
    - Unified opcodes are accepted directly.
    - Task opcodes (``task.subagent`` / ``task.tool`` / salvage ``task``) are mapped to the existing
      tool call surface (``tasks.spawn``) to preserve runtime behavior.
    """

    data = _coerce_mapping(raw)
    if data is None:
        raise ValueError("action_payload_unparseable")

    payload = _normalize_to_unified_payload(data)
    return PlannerAction.model_validate(payload)


def try_normalize_action(raw: str) -> PlannerAction | None:
    """Best-effort normalization that never raises (used by salvage paths)."""

    try:
        return normalize_action(raw)
    except (ValidationError, ValueError, TypeError):
        return None


def _coerce_mapping(raw: str | Mapping[str, Any]) -> Mapping[str, Any] | None:
    if isinstance(raw, Mapping):
        return raw

    text = raw.strip()
    if not text:
        return None

    extracted = _extract_json_object(text)
    try:
        parsed = json.loads(extracted)
        return parsed if isinstance(parsed, Mapping) else None
    except Exception:
        try:
            parsed = ast.literal_eval(extracted)
            return parsed if isinstance(parsed, Mapping) else None
        except Exception:
            return None


def _extract_json_object(text: str) -> str:
    """Extract a JSON object from fenced or mixed text."""

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _normalize_to_unified_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    # Legacy-ish: has legacy keys or null next_node.
    if (
        "thought" in data
        or "plan" in data
        or "join" in data
        or ("next_node" in data and data.get("next_node") is None)
    ):
        return _normalize_legacy_shape(data)

    next_node = data.get("next_node")
    args = data.get("args")

    if isinstance(next_node, str):
        if next_node == "final_response":
            return _normalize_unified_final(args, thought=data.get("thought"))
        if next_node in {"parallel", "plan"}:
            return _normalize_unified_parallel(args, thought=data.get("thought"))
        if next_node in {"task.subagent", "task.tool", "task"}:
            return _normalize_unified_task(next_node, args, thought=data.get("thought"))
        return _normalize_unified_tool_call(next_node, args, thought=data.get("thought"))

    # Hybrid/unexpected: treat as finish attempt if there's an answer-like payload.
    if isinstance(args, Mapping) and any(key in args for key in ("raw_answer", "answer")):
        return _normalize_unified_final(args, thought=data.get("thought"))
    return _normalize_legacy_shape(data)


def _normalize_legacy_shape(data: Mapping[str, Any]) -> dict[str, Any]:
    patched: dict[str, Any] = dict(data)

    if "action" in patched and isinstance(patched["action"], Mapping):
        nested = patched["action"]
        patched.update(
            {
                "thought": nested.get("thought", patched.get("thought")),
                "next_node": nested.get("next_node", patched.get("next_node")),
                "args": nested.get("args", patched.get("args")),
                "plan": nested.get("plan", patched.get("plan")),
                "join": nested.get("join", patched.get("join")),
            }
        )

    thought = patched.get("thought")
    thought_text = thought if isinstance(thought, str) and thought.strip() else _DEFAULT_THOUGHT

    # Case 1: Parallel plan (legacy: plan/join at top-level)
    plan = _normalize_plan_list(patched.get("plan"))
    join = _normalize_join(patched.get("join"))
    if plan is not None:
        return {
            "thought": thought_text,
            "next_node": "parallel",
            "args": {
                "steps": plan,
                "join": join,
            },
        }

    # Case 2: Terminal (legacy: next_node=null with args.raw_answer)
    if patched.get("next_node") is None:
        args = dict(patched.get("args") or {}) if isinstance(patched.get("args"), Mapping) else {}
        answer = _extract_answer_value(args)
        if answer is not None:
            args.setdefault("answer", answer)
            args.setdefault("raw_answer", answer)
        return {
            "thought": thought_text,
            "next_node": "final_response",
            "args": args,
        }

    # Case 3: Tool call
    next_node = patched.get("next_node")
    args = dict(patched.get("args") or {}) if isinstance(patched.get("args"), Mapping) else {}
    if not isinstance(next_node, str) or not next_node.strip():
        # Salvage: ambiguous tool name; treat as finish attempt if answer exists.
        answer = _extract_answer_value(args)
        if answer is not None:
            args.setdefault("answer", answer)
            args.setdefault("raw_answer", answer)
            return {
                "thought": thought_text,
                "next_node": "final_response",
                "args": args,
            }
        return {
            "thought": thought_text,
            "next_node": "final_response",
            "args": {"answer": "", "raw_answer": ""},
        }

    return {
        "thought": thought_text,
        "next_node": next_node,
        "args": args,
    }


def _normalize_unified_tool_call(next_node: str, args: Any, *, thought: Any = None) -> dict[str, Any]:
    return {
        "next_node": next_node,
        "args": dict(args) if isinstance(args, Mapping) else {},
        "thought": str(thought).strip() if isinstance(thought, str) and thought.strip() else _DEFAULT_THOUGHT,
    }


def _normalize_unified_final(args: Any, *, thought: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = dict(args) if isinstance(args, Mapping) else {}
    answer = _extract_answer_value(payload)
    if answer is not None:
        payload.setdefault("answer", answer)
        payload.setdefault("raw_answer", answer)

    return {
        "next_node": "final_response",
        "args": payload or {},
        "thought": str(thought).strip() if isinstance(thought, str) and thought.strip() else _DEFAULT_THOUGHT,
    }


def _normalize_unified_parallel(args: Any, *, thought: Any = None) -> dict[str, Any]:
    payload = dict(args) if isinstance(args, Mapping) else {}
    steps = payload.get("steps")
    join = payload.get("join")
    return {
        "next_node": "parallel",
        "args": {
            "steps": _normalize_plan_list(steps) or [],
            "join": _normalize_join(join),
        },
        "thought": str(thought).strip() if isinstance(thought, str) and thought.strip() else _DEFAULT_THOUGHT,
    }


def _normalize_unified_task(next_node: str, args: Any, *, thought: Any = None) -> dict[str, Any]:
    payload = dict(args) if isinstance(args, Mapping) else {}
    payload = _canonicalize_task_spawn_payload(next_node, payload)
    return {
        "next_node": "tasks.spawn",
        "args": payload,
        "thought": str(thought).strip() if isinstance(thought, str) and thought.strip() else _DEFAULT_THOUGHT,
    }


def _canonicalize_task_spawn_payload(next_node: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Convert RFC task args into tasks.spawn args (TasksSpawnArgs schema)."""

    def _merge_value(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        normalized = text.lower().strip().replace("-", "_").replace(" ", "_")
        if normalized in {"append", "replace", "human_gated"}:
            return normalized
        if normalized.startswith("human"):
            return "human_gated"
        return None

    merge = _merge_value(payload.get("merge_strategy"))
    if merge is not None:
        payload["merge_strategy"] = merge

    group_merge = _merge_value(payload.get("group_merge_strategy"))
    if group_merge is not None:
        payload["group_merge_strategy"] = group_merge

    # Enforce mode and field names for tasks.spawn.
    if next_node == "task.subagent":
        payload["mode"] = "subagent"
        payload.pop("tool", None)
        payload.pop("tool_args", None)
        payload.pop("tool_name", None)
        return payload

    if next_node == "task.tool":
        payload["mode"] = "job"
        tool = payload.pop("tool", None)
        tool_args = payload.pop("tool_args", None)
        if tool is not None:
            payload["tool_name"] = tool
        if tool_args is not None:
            payload["tool_args"] = tool_args
        payload.pop("query", None)
        return payload

    # Salvage alias: next_node == "task"
    if isinstance(payload.get("query"), str):
        payload["mode"] = "subagent"
        payload.pop("tool", None)
        payload.pop("tool_args", None)
        payload.pop("tool_name", None)
        return payload

    tool_name = payload.get("tool_name") or payload.get("tool")
    if tool_name is not None:
        payload["mode"] = "job"
        if "tool_name" not in payload:
            payload["tool_name"] = str(payload.pop("tool"))
        if "tool_args" not in payload:
            tool_args = payload.pop("tool_args", None)
            if tool_args is not None:
                payload["tool_args"] = tool_args
        payload.pop("query", None)
        return payload

    return payload


def _normalize_plan_list(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    normalised: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        entry = dict(item)
        if "node" not in entry:
            continue
        entry.setdefault("args", {})
        normalised.append(entry)
    return normalised or None


def _normalize_join(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    join = dict(value)
    join.setdefault("inject", None)
    join.setdefault("args", {})
    # If the join has no node (RFC allows node=None), omit it to avoid validation errors.
    if join.get("node") in (None, ""):
        return None
    return join


def _extract_answer_value(payload: Mapping[str, Any]) -> str | None:
    answer = payload.get("answer")
    if isinstance(answer, str) and answer.strip():
        return answer
    raw_answer = payload.get("raw_answer")
    if isinstance(raw_answer, str) and raw_answer.strip():
        return raw_answer
    return None
