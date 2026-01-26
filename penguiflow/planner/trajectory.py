"""Trajectory state and serialisation helpers."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from .migration import dump_action_legacy, normalize_action
from .models import PlannerAction


class TrajectorySummary(BaseModel):
    goals: list[str] = []
    facts: dict[str, Any] = {}
    pending: list[str] = []
    last_output_digest: str | None = None
    note: str | None = None

    def compact(self) -> dict[str, Any]:
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
    """Result from a completed background task, stored in trajectory."""

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
    action: PlannerAction
    observation: Any | None = None
    llm_observation: Any | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None
    streams: Mapping[str, Sequence[Mapping[str, Any]]] | None = None

    def dump(self) -> dict[str, Any]:
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
        if self.llm_observation is not None:
            return self.llm_observation
        return self._serialise_observation()


@dataclass(slots=True)
class Trajectory:
    query: str
    llm_context: Mapping[str, Any] | None = None
    tool_context: dict[str, Any] | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    sources: list[Mapping[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    steps: list[TrajectoryStep] = field(default_factory=list)
    summary: TrajectorySummary | None = None
    hint_state: dict[str, Any] = field(default_factory=dict)
    resume_user_input: str | None = None
    steering_inputs: list[str] = field(default_factory=list)
    background_results: dict[str, BackgroundTaskResult] = field(default_factory=dict)

    def to_history(self) -> list[dict[str, Any]]:
        return [step.dump() for step in self.steps]

    def add_background_result(self, result: BackgroundTaskResult) -> None:
        self.background_results[result.task_id] = result

    def mark_background_consumed(self, task_id: str) -> bool:
        if task_id in self.background_results:
            self.background_results[task_id].consumed = True
            return True
        return False

    def clear_consumed_background(self) -> int:
        to_remove = [task_id for task_id, result in self.background_results.items() if result.consumed]
        for task_id in to_remove:
            del self.background_results[task_id]
        return len(to_remove)

    def get_unconsumed_background(self) -> dict[str, BackgroundTaskResult]:
        return {task_id: result for task_id, result in self.background_results.items() if not result.consumed}

    def serialise(self) -> dict[str, Any]:
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
            "artifacts": dict(self.artifacts),
            "sources": [dict(src) for src in self.sources],
            "metadata": dict(self.metadata),
            "steps": self.to_history(),
            "summary": self.summary.model_dump(mode="json") if self.summary else None,
            "hint_state": dict(self.hint_state),
            "resume_user_input": self.resume_user_input,
            "steering_inputs": list(self.steering_inputs),
            "background_results": {task_id: result.to_payload() for task_id, result in self.background_results.items()},
        }

    @classmethod
    def from_serialised(cls, payload: Mapping[str, Any]) -> Trajectory:
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
        return trajectory

    def compress(self) -> TrajectorySummary:
        facts: dict[str, Any] = {}
        pending: list[str] = []
        last_observation = None
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
