"""Planner-native helpers for writing eval metric internals.

Why: metrics often need a stable node sequence projection from planner traces,
and that parsing should not be rebuilt in every project example.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping

from pydantic import BaseModel

from penguiflow.llm import LLMClient, LLMMessage, TextPart

_ONE_OF_CLIENT_OR_MODEL = "llm_judge requires exactly one of client or model"


class _JudgeResult(BaseModel):
    score: float
    feedback: str | None = None


def extract_node_sequence(pred_trace: object) -> list[str]:
    """Return the observed planner node sequence from ``steps[].action.next_node``.

    Why: route-style metrics care about stable planner node choices, not the full
    serialized trace shape. Missing or malformed trace parts should degrade to an
    empty or partial sequence instead of breaking metric execution.
    """

    if not isinstance(pred_trace, Mapping):
        return []
    steps = pred_trace.get("steps")
    if not isinstance(steps, list):
        return []

    sequence: list[str] = []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        action = step.get("action")
        if not isinstance(action, Mapping):
            continue
        next_node = action.get("next_node")
        if isinstance(next_node, str) and next_node:
            sequence.append(next_node)
    return sequence


def extract_terminal_node(pred_trace: object) -> str | None:
    """Return the last observed planner node, if any.

    Why: many route metrics only need the terminal planner choice and should not
    repeat malformed-trace handling in every metric implementation.
    """

    sequence = extract_node_sequence(pred_trace)
    return sequence[-1] if sequence else None


def extract_step_args(pred_trace: object, node_name: str | None = None) -> list[dict[str, object]]:
    """Return planner action arg mappings, optionally filtered by node name.

    Why: argument-aware metrics need stable access to planner step args without
    reimplementing malformed-trace handling or planner step traversal.
    """

    if not isinstance(pred_trace, Mapping):
        return []
    steps = pred_trace.get("steps")
    if not isinstance(steps, list):
        return []

    args_list: list[dict[str, object]] = []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        action = step.get("action")
        if not isinstance(action, Mapping):
            continue
        next_node = action.get("next_node")
        if node_name is not None and next_node != node_name:
            continue
        args = action.get("args")
        if isinstance(args, Mapping):
            args_list.append(dict(args))
    return args_list


def extract_step_subset(
    pred_trace: object,
    node_name: str | None = None,
    fields: list[str] | None = None,
) -> list[dict[str, object]]:
    """Return full or field-selected planner action arg mappings.

    Why: some metrics compare only stable argument slices and should not expose
    raw planner trace traversal or ad hoc field-picking logic.
    """

    args_list = extract_step_args(pred_trace, node_name=node_name)
    if fields is None:
        return args_list

    subsets: list[dict[str, object]] = []
    for args in args_list:
        subset = {field: args[field] for field in fields if field in args}
        subsets.append(subset)
    return subsets


def _is_ordered_subsequence(sequence: list[str], expected: list[str]) -> bool:
    if not expected:
        return True

    index = 0
    for item in sequence:
        if item == expected[index]:
            index += 1
            if index == len(expected):
                return True
    return False


def sequence_match(
    actual: list[str],
    expected: list[str],
    mode: str = "strict",
) -> bool:
    """Compare planner node sequences using a small set of route-oriented modes.

    Why: route metrics need readable semantics like exact order or extra-calls
    allowed without rebuilding comparison logic in every project metric.
    """

    if mode == "strict":
        return actual == expected
    if mode == "unordered":
        return Counter(actual) == Counter(expected)
    if mode == "subset":
        return _is_ordered_subsequence(actual, expected)
    if mode == "superset":
        return _is_ordered_subsequence(expected, actual)
    raise ValueError(f"unsupported sequence_match mode: {mode}")


def step_args_match(
    actual: Mapping[str, object],
    expected: Mapping[str, object],
    mode: str = "subset",
    fields: list[str] | None = None,
) -> bool:
    """Compare planner arg mappings using simple containment semantics.

    Why: metrics often validate only a stable subset of planner call arguments,
    and that check should stay small and explicit.
    """

    if fields is not None:
        actual = {field: actual[field] for field in fields if field in actual}
        expected = {field: expected[field] for field in fields if field in expected}

    if mode == "subset":
        return all(key in actual and actual[key] == value for key, value in expected.items())
    if mode == "superset":
        return all(key in expected and expected[key] == value for key, value in actual.items())
    raise ValueError(f"unsupported step_args_match mode: {mode}")


def trajectory_subset_match(
    pred_trace: object,
    expected_subset: Mapping[str, object],
    mode: str = "subset",
) -> bool:
    """Match planner-native trajectory expectations against an observed trace.

    Why: route and call-shape metrics need one small helper that composes the
    existing projection primitives without exposing raw JSON matching semantics.
    """

    if mode not in {"subset", "superset"}:
        raise ValueError(f"unsupported trajectory_subset_match mode: {mode}")

    node_sequence = expected_subset.get("node_sequence")
    if isinstance(node_sequence, list):
        actual_sequence = extract_node_sequence(pred_trace)
        if not sequence_match(actual_sequence, node_sequence, mode=mode):
            return False

    step_args = expected_subset.get("step_args")
    if isinstance(step_args, list):
        node_name = expected_subset.get("node_name")
        observed_args = extract_step_args(pred_trace, node_name=node_name if isinstance(node_name, str) else None)
        for expected_args in step_args:
            if not isinstance(expected_args, Mapping):
                return False
            if not any(step_args_match(actual, expected_args, mode=mode) for actual in observed_args):
                return False

    return True


def _format_judge_payload(label: str, value: object) -> str:
    return f"{label}:\n{json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)}"


async def llm_judge(
    *,
    prompt: str,
    inputs: object | None = None,
    outputs: object | None = None,
    reference_outputs: object | None = None,
    client: LLMClient | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    use_reasoning: bool = True,
) -> dict[str, object]:
    """Run a native structured LLM judge and normalize the result for metrics.

    Why: qualitative metrics should reuse one small native LLM glue path instead
    of rebuilding prompt packaging and score/feedback parsing per project.
    """

    del use_reasoning
    # Spelled out rather than as an XOR so the type checker can narrow `model`
    # to `str` on the branch that constructs a client from it.
    if client is not None:
        if model is not None:
            raise ValueError(_ONE_OF_CLIENT_OR_MODEL)
        judge_client = client
    elif model is None:
        raise ValueError(_ONE_OF_CLIENT_OR_MODEL)
    else:
        judge_client = LLMClient(model)

    sections = [prompt.strip()]
    if inputs is not None:
        sections.append(_format_judge_payload("inputs", inputs))
    if outputs is not None:
        sections.append(_format_judge_payload("outputs", outputs))
    if reference_outputs is not None:
        sections.append(_format_judge_payload("reference_outputs", reference_outputs))

    message = LLMMessage(role="user", parts=[TextPart(text="\n\n".join(sections))])
    try:
        result = await judge_client.generate([message], _JudgeResult, temperature=temperature)
    except Exception as exc:
        raise RuntimeError(f"llm_judge failed: {exc}") from exc
    # Raised outside the try above so it is not re-wrapped as "llm_judge failed".
    data = result.data
    if not isinstance(data, _JudgeResult):
        raise RuntimeError(f"llm_judge received an unexpected response shape: {type(data).__name__}")
    return {"score": data.score, "feedback": data.feedback}
