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


def _action_candidate_score(data: Mapping[str, Any]) -> int:
    """Heuristic score for selecting the best JSON object from mixed outputs."""

    score = 0
    if "action" in data and isinstance(data.get("action"), Mapping):
        score += 5
    if "next_node" in data:
        score += 10
        next_node = data.get("next_node")
        if isinstance(next_node, str) and next_node.strip():
            score += 2
    # Legacy-ish markers.
    if "thought" in data:
        score += 1
    if "plan" in data or "join" in data:
        score += 1
    if "args" in data:
        score += 2
        args = data.get("args")
        if isinstance(args, Mapping):
            score += 1
        elif isinstance(args, str) and args.strip():
            score += 1
    return score


def _iter_json_objects(text: str) -> list[object]:
    """Extract JSON values from a mixed string.

    Uses json.JSONDecoder.raw_decode to parse the *first complete* JSON value
    starting at each candidate '{' or '['. This is robust to trailing prose or
    multiple JSON objects concatenated together.
    """

    decoder = json.JSONDecoder()
    objs: list[object] = []
    idx = 0
    n = len(text)
    while idx < n:
        next_obj = text.find("{", idx)
        next_arr = text.find("[", idx)
        if next_obj == -1 and next_arr == -1:
            break
        if next_obj == -1:
            start = next_arr
        elif next_arr == -1:
            start = next_obj
        else:
            start = min(next_obj, next_arr)

        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue

        objs.append(obj)
        # Continue scanning after the parsed value.
        idx = max(end, start + 1)
    return objs


def _iter_json_spans(text: str) -> list[tuple[object, int, int]]:
    """Extract JSON values with character spans in the original text."""

    decoder = json.JSONDecoder()
    spans: list[tuple[object, int, int]] = []
    idx = 0
    n = len(text)
    while idx < n:
        next_obj = text.find("{", idx)
        next_arr = text.find("[", idx)
        if next_obj == -1 and next_arr == -1:
            break
        if next_obj == -1:
            start = next_arr
        elif next_arr == -1:
            start = next_obj
        else:
            start = min(next_obj, next_arr)

        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue

        spans.append((obj, start, end))
        idx = max(end, start + 1)
    return spans


def _iter_json_fence_contents(text: str) -> list[tuple[str, int]]:
    """Extract fenced code blocks (```json ... ```) contents."""

    blocks: list[tuple[str, int]] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE):
        content = match.group(1)
        if content:
            blocks.append((content.strip(), match.start(1)))
    return blocks


def _truncate_preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _coerce_mapping_from_text(text: str) -> tuple[Mapping[str, Any] | None, dict[str, Any] | None]:
    """Best-effort extraction of an action-like mapping from mixed model output."""

    candidates: list[tuple[Mapping[str, Any], dict[str, Any]]] = []

    # 1) Try fenced blocks first (models often wrap JSON there).
    for block, base in _iter_json_fence_contents(text):
        for obj, start, end in _iter_json_spans(block):
            if isinstance(obj, Mapping):
                meta = {"source": "fence", "start": base + start, "end": base + end}
                candidates.append((obj, meta))
            elif isinstance(obj, str) and "{" in obj:
                # Some providers double-encode JSON as a string.
                for inner in _iter_json_objects(obj):
                    if isinstance(inner, Mapping):
                        meta = {"source": "embedded_string", "start": None, "end": None}
                        candidates.append((inner, meta))

    # 2) Scan the whole text for JSON objects/arrays.
    for obj, start, end in _iter_json_spans(text):
        if isinstance(obj, Mapping):
            meta = {"source": "text", "start": start, "end": end}
            candidates.append((obj, meta))
        elif isinstance(obj, list):
            # Weak-model pattern: returns a list of candidate actions.
            for item in obj:
                if isinstance(item, Mapping):
                    meta = {"source": "list_item", "start": start, "end": end}
                    candidates.append((item, meta))
        elif isinstance(obj, str) and "{" in obj:
            for inner in _iter_json_objects(obj):
                if isinstance(inner, Mapping):
                    meta = {"source": "embedded_string", "start": None, "end": None}
                    candidates.append((inner, meta))

    if not candidates:
        return None, None

    # Prefer action-shaped payloads over generic dicts.
    actionish: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    for c, meta in candidates:
        if (
            "next_node" in c
            or "thought" in c
            or "plan" in c
            or "join" in c
            or ("action" in c and isinstance(c.get("action"), Mapping))
        ):
            actionish.append((c, meta))

    # If we didn't find any action-shaped objects, avoid selecting nested args dicts
    # (common when the outer JSON object is truncated). The only exception is when
    # the dict itself looks like a final payload (answer/raw_answer), which we can
    # still salvage.
    if actionish:
        pool = actionish
    else:
        answerish: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
        for c, meta in candidates:
            if any(key in c for key in ("raw_answer", "answer")):
                answerish.append((c, meta))
        if not answerish:
            return None, None
        pool = answerish
    # Deterministic: higher score first, then earlier start index (when known).
    def _sort_key(item: tuple[Mapping[str, Any], dict[str, Any]]) -> tuple[int, int]:
        c, meta = item
        start = meta.get("start")
        start_i = int(start) if isinstance(start, int) else 10**9
        return (_action_candidate_score(c), -start_i)

    selected, selected_meta = sorted(pool, key=_sort_key, reverse=True)[0]

    # Build debug: show what was ignored outside the selected JSON span.
    selected_start = selected_meta.get("start")
    selected_end = selected_meta.get("end")
    debug: dict[str, Any] = {
        "selected_source": selected_meta.get("source"),
        "selected_next_node": selected.get("next_node"),
        "candidate_count": len(candidates),
    }

    # Summarize other JSON candidates for debugging (keys only; avoid huge payloads).
    others: list[dict[str, Any]] = []
    for c, meta in candidates:
        if c is selected and meta is selected_meta:
            continue
        others.append(
            {
                "source": meta.get("source"),
                "next_node": c.get("next_node"),
                "keys": list(c.keys())[:12],
                "score": _action_candidate_score(c),
            }
        )
    debug["other_json_count"] = len(others)
    debug["other_json_summaries"] = others[:5]

    # Provide other action candidates (normalized shape) for runtime fallbacks.
    # Order by appearance in the text when possible.
    def _meta_start(m: dict[str, Any]) -> int:
        start = m.get("start")
        return int(start) if isinstance(start, int) else 10**9

    ordered = sorted(candidates, key=lambda item: _meta_start(item[1]))
    other_actions: list[dict[str, Any]] = []
    for c, meta in ordered:
        if c is selected and meta is selected_meta:
            continue
        try:
            payload = _normalize_to_unified_payload(c)
            candidate_action = PlannerAction.model_validate(payload)
        except Exception:
            continue
        # Store only minimal fields; mask large answers for safety.
        args_out = dict(candidate_action.args or {})
        if candidate_action.next_node == "final_response":
            answer_val = args_out.get("answer") or args_out.get("raw_answer")
            if isinstance(answer_val, str) and answer_val:
                args_out = {"answer_len": len(answer_val)}
        other_actions.append({"next_node": candidate_action.next_node, "args": args_out})
        if len(other_actions) >= 5:
            break
    debug["other_actions"] = other_actions

    if (
        isinstance(selected_start, int)
        and isinstance(selected_end, int)
        and 0 <= selected_start <= selected_end <= len(text)
    ):
        prefix = text[:selected_start]
        suffix = text[selected_end:]
        ignored = (prefix + suffix).strip()
        debug.update(
            {
                "selected_start": selected_start,
                "selected_end": selected_end,
                "ignored_prefix_len": len(prefix),
                "ignored_suffix_len": len(suffix),
                "ignored_text_len": len(ignored),
                "ignored_prefix_preview": _truncate_preview(prefix.strip(), 400),
                "ignored_suffix_preview": _truncate_preview(suffix.strip(), 400),
                "ignored_text_preview": _truncate_preview(ignored, 1200),
            }
        )
    else:
        # Unknown span (e.g., embedded string) – only provide coarse info.
        debug["ignored_text_len"] = None

    return selected, debug


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

    # Fast path: direct JSON object.
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, Mapping) else None
        except Exception:
            pass

    # Robust path: scan mixed text (reasoning + JSON, fenced blocks, multiple objects).
    parsed, _debug = _coerce_mapping_from_text(text)
    if parsed is not None:
        return parsed

    # Legacy fallback: attempt to extract a single object via naive slicing + python literal eval.
    extracted = _extract_json_object(text)
    try:
        parsed2 = json.loads(extracted)
        return parsed2 if isinstance(parsed2, Mapping) else None
    except Exception:
        try:
            parsed2 = ast.literal_eval(extracted)
            return parsed2 if isinstance(parsed2, Mapping) else None
        except Exception:
            return None


def _extract_json_object(text: str) -> str:
    """Extract a JSON object from fenced or mixed text."""

    # NOTE: This is intentionally a legacy fallback. Prefer _coerce_mapping_from_text(),
    # which uses a real JSON decoder to identify complete objects.
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def normalize_action_with_debug(raw: str | Mapping[str, Any]) -> tuple[PlannerAction, dict[str, Any] | None]:
    """Normalize an action and return parse diagnostics for mixed outputs."""

    if isinstance(raw, Mapping):
        payload = _normalize_to_unified_payload(raw)
        return PlannerAction.model_validate(payload), None

    text = raw.strip()
    if not text:
        raise ValueError("action_payload_unparseable")

    # Fast path: direct JSON object.
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, Mapping):
                payload = _normalize_to_unified_payload(parsed)
                return PlannerAction.model_validate(payload), None
        except Exception:
            pass

    parsed, debug = _coerce_mapping_from_text(text)
    if parsed is not None:
        payload = _normalize_to_unified_payload(parsed)
        return PlannerAction.model_validate(payload), debug

    # Legacy fallback.
    parsed2 = _coerce_mapping(text)
    if parsed2 is None:
        raise ValueError("action_payload_unparseable")
    payload = _normalize_to_unified_payload(parsed2)
    return PlannerAction.model_validate(payload), None


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

    # Weak-model fallback: sometimes returns final payload directly without wrapping in args/next_node.
    if any(key in data for key in ("raw_answer", "answer")):
        return _normalize_unified_final(data, thought=data.get("thought"))

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
    # Handle case where weak model puts answer directly in args as a string
    # e.g., {"next_node": "final_response", "args": "Here is my answer"}
    if isinstance(args, str) and args.strip():
        payload = {"answer": args.strip(), "raw_answer": args.strip()}
    elif isinstance(args, Mapping):
        payload = dict(args)
    else:
        payload = {}

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
