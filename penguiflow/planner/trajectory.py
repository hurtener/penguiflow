"""Trajectory state and serialisation helpers."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from ..llm.types import AudioPart, ContentPart, ImagePart
from .migration import dump_action_legacy, normalize_action
from .models import PlannerAction


class TrajectorySummary(BaseModel):
    """Compact, LLM-facing summary of a trajectory's progress.

    Produced by :meth:`Trajectory.compress` (or restored via ``model_validate``) to
    give callers/LLMs a bounded, JSON-serialisable snapshot of what a run has
    accomplished so far.

    Attributes:
        goals: List of goal statements the trajectory is working toward.
        facts: Arbitrary key/value facts gathered while executing the trajectory.
        pending: List of pending follow-up actions (e.g., retries) still outstanding.
        last_output_digest: Truncated string digest of the most recent observation,
            if any.
        note: Optional free-form annotation describing how the summary was produced.
    """

    goals: list[str] = []
    facts: dict[str, Any] = {}
    pending: list[str] = []
    last_output_digest: str | None = None
    note: str | None = None

    def compact(self) -> dict[str, Any]:
        """Return a compact JSON-serialisable dict view of this summary.

        Omits the ``note`` field entirely when it is falsy, keeping the payload
        minimal for contexts (e.g. LLM prompts) where every token counts.

        Returns:
            dict[str, Any]: A dict with ``goals``, ``facts``, ``pending``, and
            ``last_output_digest`` keys, plus ``note`` when set.
        """
        payload = {
            "goals": list(self.goals),
            "facts": dict(self.facts),
            "pending": list(self.pending),
            "last_output_digest": self.last_output_digest,
        }
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass(slots=True)
class BackgroundTaskResult:
    """Result from a completed background task, stored in trajectory.

    Attributes:
        task_id: Unique identifier of the background task this result belongs to.
        group_id: Optional identifier grouping related background tasks together.
        status: Terminal status of the task, either ``"completed"`` or ``"failed"``.
        summary: Optional human-readable summary of the task's outcome.
        payload: Arbitrary result payload produced by the task.
        facts: Structured facts extracted from the task's execution.
        artifacts: List of artifact records (as dicts) produced by the task.
        consumed: Whether this result has already been consumed by the planner.
        completed_at: Unix timestamp (seconds) when the task completed.
    """

    task_id: str
    group_id: str | None = None
    status: Literal["completed", "failed"] = "completed"
    summary: str | None = None
    payload: Any = None
    facts: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    consumed: bool = False
    completed_at: float = field(default_factory=time.time)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> BackgroundTaskResult | None:
        """Build a :class:`BackgroundTaskResult` from a raw mapping payload.

        Args:
            payload: Mapping containing at least a ``task_id`` key, plus optional
                ``status``, ``summary``, ``digest``, ``facts``, ``artifacts``,
                ``group_id``, ``payload``, ``consumed``, and ``completed_at`` keys.

        Returns:
            BackgroundTaskResult | None: The constructed result, or ``None`` if
            ``payload`` does not contain a valid non-empty string ``task_id``.
        """
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return None
        status_value = payload.get("status")
        status: Literal["completed", "failed"] = "failed" if status_value == "failed" else "completed"
        summary = payload.get("summary")
        digest = payload.get("digest")
        if summary is None and isinstance(digest, list):
            summary = " ".join(str(item) for item in digest if item)
        facts_value = payload.get("facts")
        artifacts_value = payload.get("artifacts")
        completed_at = payload.get("completed_at")
        if not isinstance(completed_at, (int, float)):
            completed_at = None
        return cls(
            task_id=task_id,
            group_id=payload.get("group_id") if isinstance(payload.get("group_id"), str) else None,
            status=status,
            summary=summary,
            payload=payload.get("payload"),
            facts=dict(facts_value) if isinstance(facts_value, Mapping) else {},
            artifacts=[dict(item) for item in artifacts_value if isinstance(item, Mapping)]
            if isinstance(artifacts_value, list)
            else [],
            consumed=bool(payload.get("consumed", False)),
            completed_at=float(completed_at) if completed_at is not None else time.time(),
        )

    def to_payload(self) -> dict[str, Any]:
        """Serialise this result to a JSON-safe dict.

        Returns:
            dict[str, Any]: A dict with ``task_id``, ``group_id``, ``status``,
            ``summary``, ``payload`` (passed through JSON-safe coercion), ``facts``,
            ``artifacts``, ``consumed``, and ``completed_at`` keys.
        """
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "group_id": self.group_id,
            "status": self.status,
            "summary": self.summary,
            "payload": _safe_json_payload(self.payload),
            "facts": dict(self.facts),
            "artifacts": [dict(item) for item in self.artifacts],
            "consumed": self.consumed,
            "completed_at": self.completed_at,
        }
        return payload


def _safe_json_payload(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return None


def _input_part_stub(part: ContentPart) -> dict[str, Any] | None:
    if isinstance(part, ImagePart):
        return {
            "type": "image",
            "media_type": part.media_type,
            "bytes": len(part.data),
            "detail": part.detail,
        }
    if isinstance(part, AudioPart):
        return {
            "type": "audio",
            "media_type": part.media_type,
            "bytes": len(part.data),
        }
    return None


def _input_part_stubs(parts: Sequence[ContentPart]) -> list[dict[str, Any]]:
    return [stub for part in parts if (stub := _input_part_stub(part)) is not None]


def coerce_background_results(raw: Any) -> dict[str, BackgroundTaskResult]:
    if raw is None:
        return {}
    items: list[Any] = []
    if isinstance(raw, BackgroundTaskResult):
        items = [raw]
    elif isinstance(raw, Mapping):
        if "task_id" in raw:
            items = [raw]
        else:
            items = list(raw.values())
    elif isinstance(raw, list):
        items = raw
    results: dict[str, BackgroundTaskResult] = {}
    for item in items:
        result: BackgroundTaskResult | None
        if isinstance(item, BackgroundTaskResult):
            result = item
        elif isinstance(item, Mapping):
            result = BackgroundTaskResult.from_payload(item)
        else:
            result = None
        if result is None:
            continue
        results[result.task_id] = result
    return results


def extract_background_results(
    llm_context: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, BackgroundTaskResult]]:
    if not isinstance(llm_context, Mapping):
        return None, {}
    extracted: list[Any] = []
    if "background_result" in llm_context:
        extracted.append(llm_context.get("background_result"))
    if "background_results" in llm_context:
        extracted.append(llm_context.get("background_results"))
    if not extracted:
        return dict(llm_context), {}
    results: dict[str, BackgroundTaskResult] = {}
    for item in extracted:
        results.update(coerce_background_results(item))
    cleaned = {
        key: value for key, value in llm_context.items() if key not in {"background_result", "background_results"}
    }
    return cleaned, results


@dataclass(slots=True)
class TrajectoryStep:
    """A single recorded step (action + observation) within a :class:`Trajectory`.

    Attributes:
        action: The planner action that was executed for this step.
        observation: Raw observation/result returned by executing ``action``, if any.
        llm_observation: Optional pre-serialised observation tailored for LLM
            consumption, used in place of ``observation`` when present.
        error: Optional error message if the step failed.
        failure: Optional mapping with structured failure details.
        streams: Optional mapping of stream id to the sequence of chunks emitted on
            that stream during this step.
    """

    action: PlannerAction
    observation: Any | None = None
    llm_observation: Any | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None
    streams: Mapping[str, Sequence[Mapping[str, Any]]] | None = None

    def dump(self) -> dict[str, Any]:
        """Serialise this step to a plain, JSON-safe dict.

        Returns:
            dict[str, Any]: A dict with ``action`` (legacy-format action dump),
            ``observation``, ``error``, and ``failure`` keys, plus ``llm_observation``
            and ``streams`` when set.
        """
        payload: dict[str, Any] = {
            "action": dump_action_legacy(self.action),
            "observation": self._serialise_observation(),
            "error": self.error,
            "failure": dict(self.failure) if self.failure else None,
        }
        if self.llm_observation is not None:
            payload["llm_observation"] = self.llm_observation
        if self.streams:
            payload["streams"] = {
                stream_id: [dict(chunk) for chunk in chunks] for stream_id, chunks in self.streams.items()
            }
        return payload

    def _serialise_observation(self) -> Any:
        if isinstance(self.observation, BaseModel):
            return self.observation.model_dump(mode="json")
        return self.observation

    def serialise_for_llm(self) -> Any:
        """Return the observation representation to send to the LLM.

        Prefers ``llm_observation`` when set, otherwise falls back to the serialised
        ``observation``.

        Returns:
            Any: The LLM-facing observation value for this step.
        """
        if self.llm_observation is not None:
            return self.llm_observation
        return self._serialise_observation()


@dataclass(slots=True)
class Trajectory:
    """Mutable record of a planner run: inputs, executed steps, and derived state.

    Tracks the original query and context alongside every :class:`TrajectoryStep`
    executed so far, plus auxiliary state (artifacts, sources, background task
    results, hints) needed to resume or summarise the run.

    Attributes:
        query: The original user query that started this trajectory.
        llm_context: Optional read-only context mapping shared with the LLM.
        tool_context: Optional mutable context mapping available to tools.
        input_parts: Tuple of non-text content parts (images/audio) supplied with the
            query.
        artifacts: Named artifacts accumulated over the run.
        sources: List of source records (as mappings) collected during the run.
        metadata: Arbitrary metadata associated with the run.
        steps: Ordered list of executed :class:`TrajectoryStep` instances.
        summary: Optional compact :class:`TrajectorySummary` of progress so far.
        hint_state: Mutable state used to track planner hints across steps.
        resume_user_input: Optional user input supplied when resuming a paused run.
        steering_inputs: List of user steering messages injected during the run.
        background_results: Mapping of task id to :class:`BackgroundTaskResult` for
            completed background tasks.
    """

    query: str
    llm_context: Mapping[str, Any] | None = None
    tool_context: dict[str, Any] | None = None
    input_parts: tuple[ContentPart, ...] = ()
    artifacts: dict[str, Any] = field(default_factory=dict)
    sources: list[Mapping[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    steps: list[TrajectoryStep] = field(default_factory=list)
    summary: TrajectorySummary | None = None
    hint_state: dict[str, Any] = field(default_factory=dict)
    resume_user_input: str | None = None
    steering_inputs: list[str] = field(default_factory=list)
    background_results: dict[str, BackgroundTaskResult] = field(default_factory=dict)
    finish_reason: str | None = None
    """Why the run ended (``answer_complete`` / ``no_path`` / ``budget_exhausted``).

    Why: without it a trace that exhausted its budget is indistinguishable from
    one that answered, since a run can end early with every individual step
    succeeding.
    """
    final_answer: str | None = None
    """Answer text the run terminated with, recorded by the planner at finish.

    Why: the terminal action is not appended to ``steps``, so without this the
    answer never reaches a persisted trajectory and offline evaluation can only
    score the route taken, never the answer given.
    """

    def to_history(self) -> list[dict[str, Any]]:
        """Return the dumped history of all steps.

        Returns:
            list[dict[str, Any]]: One serialised dict (via :meth:`TrajectoryStep.dump`)
            per step, in execution order.
        """
        return [step.dump() for step in self.steps]

    def add_background_result(self, result: BackgroundTaskResult) -> None:
        """Record a completed background task result on this trajectory.

        Args:
            result: The background task result to store, keyed by its ``task_id``.
        """
        self.background_results[result.task_id] = result

    def mark_background_consumed(self, task_id: str) -> bool:
        """Mark a stored background task result as consumed.

        Args:
            task_id: Identifier of the background task result to mark.

        Returns:
            bool: True if a result with ``task_id`` was found and marked consumed,
            False if no such result exists.
        """
        if task_id in self.background_results:
            self.background_results[task_id].consumed = True
            return True
        return False

    def clear_consumed_background(self) -> int:
        """Remove all background task results already marked as consumed.

        Returns:
            int: The number of background task results that were removed.
        """
        to_remove = [task_id for task_id, result in self.background_results.items() if result.consumed]
        for task_id in to_remove:
            del self.background_results[task_id]
        return len(to_remove)

    def get_unconsumed_background(self) -> dict[str, BackgroundTaskResult]:
        """Return background task results that have not yet been consumed.

        Returns:
            dict[str, BackgroundTaskResult]: Mapping of task id to result, for
            entries whose ``consumed`` flag is False.
        """
        return {task_id: result for task_id, result in self.background_results.items() if not result.consumed}

    def serialise(self) -> dict[str, Any]:
        """Serialise the full trajectory to a plain, JSON-safe dict.

        Returns:
            dict[str, Any]: A dict capturing ``query``, ``llm_context``,
            ``tool_context``, ``input_parts`` (stubbed for binary content),
            ``artifacts``, ``sources``, ``metadata``, ``steps`` (via
            :meth:`to_history`), ``summary``, ``hint_state``, ``resume_user_input``,
            ``steering_inputs``, and ``background_results``.
        """
        tool_context: dict[str, Any] | None = None
        if self.tool_context is not None:
            try:
                tool_context = json.loads(json.dumps(self.tool_context, ensure_ascii=False))
            except (TypeError, ValueError):
                tool_context = None
        return {
            "query": self.query,
            "llm_context": dict(self.llm_context or {}),
            "tool_context": tool_context,
            "input_parts": _input_part_stubs(self.input_parts),
            "artifacts": dict(self.artifacts),
            "sources": [dict(src) for src in self.sources],
            "metadata": dict(self.metadata),
            "steps": self.to_history(),
            "summary": self.summary.model_dump(mode="json") if self.summary else None,
            "hint_state": dict(self.hint_state),
            "resume_user_input": self.resume_user_input,
            "steering_inputs": list(self.steering_inputs),
            "background_results": {task_id: result.to_payload() for task_id, result in self.background_results.items()},
            "final_answer": self.final_answer,
            "finish_reason": self.finish_reason,
        }

    @classmethod
    def from_serialised(cls, payload: Mapping[str, Any]) -> Trajectory:
        """Reconstruct a :class:`Trajectory` from a previously serialised payload.

        Args:
            payload: Mapping produced by :meth:`serialise` (or a compatible legacy
                shape), containing at minimum a ``query`` key.

        Returns:
            Trajectory: A new trajectory populated from ``payload``, including
            restored steps, summary, background results, and auxiliary state.
        """
        llm_context = payload.get("llm_context") or payload.get("context_meta")
        tool_context = payload.get("tool_context")
        if not isinstance(tool_context, Mapping):
            tool_context = None
        trajectory = cls(
            query=payload["query"],
            llm_context=llm_context,
            tool_context=dict(tool_context or {}),
        )
        if isinstance(payload.get("metadata"), Mapping):
            trajectory.metadata.update(dict(payload["metadata"]))
        for step_data in payload.get("steps", []):
            action = normalize_action(step_data["action"])
            streams_payload = step_data.get("streams")
            normalised_streams: dict[str, tuple[Mapping[str, Any], ...]] | None = None
            if isinstance(streams_payload, Mapping):
                normalised_streams = {}
                for stream_id, chunk_list in streams_payload.items():
                    if not isinstance(chunk_list, Sequence):
                        continue
                    chunks: list[Mapping[str, Any]] = []
                    for chunk in chunk_list:
                        if isinstance(chunk, Mapping):
                            chunks.append(dict(chunk))
                    if chunks:
                        normalised_streams[str(stream_id)] = tuple(chunks)
            step = TrajectoryStep(
                action=action,
                observation=step_data.get("observation"),
                llm_observation=step_data.get("llm_observation"),
                error=step_data.get("error"),
                failure=step_data.get("failure"),
                streams=normalised_streams,
            )
            trajectory.steps.append(step)
        summary_data = payload.get("summary")
        if summary_data:
            trajectory.summary = TrajectorySummary.model_validate(summary_data)
        trajectory.hint_state.update(payload.get("hint_state", {}))
        trajectory.resume_user_input = payload.get("resume_user_input")
        trajectory.steering_inputs = list(payload.get("steering_inputs") or [])
        trajectory.artifacts.update(payload.get("artifacts") or {})
        for src in payload.get("sources") or []:
            if isinstance(src, Mapping):
                trajectory.sources.append(dict(src))
        background_payloads = payload.get("background_results")
        if background_payloads:
            trajectory.background_results.update(coerce_background_results(background_payloads))
        final_answer = payload.get("final_answer")
        trajectory.final_answer = final_answer if isinstance(final_answer, str) else None
        finish_reason = payload.get("finish_reason")
        trajectory.finish_reason = finish_reason if isinstance(finish_reason, str) else None
        return trajectory

    def compress(self) -> TrajectorySummary:
        """Compute and store a compact :class:`TrajectorySummary` for this trajectory.

        Derives goals, gathered facts, pending retry actions, and a truncated digest
        of the last observation from the recorded steps, using simple rule-based
        heuristics (no LLM call).

        Returns:
            TrajectorySummary: The computed summary, which is also assigned to
            ``self.summary``.
        """
        facts: dict[str, Any] = {}
        pending: list[str] = []
        last_observation = None
        input_part_stubs = _input_part_stubs(self.input_parts)
        if input_part_stubs:
            facts["input_parts"] = input_part_stubs
        if self.steps:
            last_step = self.steps[-1]
            if last_step.observation is not None:
                last_observation = last_step.serialise_for_llm()
                facts["last_observation"] = last_observation
            if last_step.error:
                facts["last_error"] = last_step.error
        for step in self.steps:
            if step.error:
                pending_target = "finish" if step.action.next_node == "final_response" else step.action.next_node
                pending.append(f"retry {pending_target}")
        digest = None
        if last_observation is not None:
            digest_raw = json.dumps(last_observation, ensure_ascii=False)
            digest = digest_raw if len(digest_raw) <= 120 else f"{digest_raw[:117]}..."
        summary = TrajectorySummary(
            goals=[self.query],
            facts=facts,
            pending=pending,
            last_output_digest=digest,
            note="rule_based",
        )
        self.summary = summary
        return summary


__all__ = [
    "BackgroundTaskResult",
    "Trajectory",
    "TrajectoryStep",
    "TrajectorySummary",
    "coerce_background_results",
    "extract_background_results",
]
