"""Trajectory state and serialisation helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

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
class TrajectoryStep:
    action: PlannerAction
    observation: Any | None = None
    llm_observation: Any | None = None
    error: str | None = None
    failure: Mapping[str, Any] | None = None
    streams: Mapping[str, Sequence[Mapping[str, Any]]] | None = None

    def dump(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action.model_dump(mode="json"),
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
    steps: list[TrajectoryStep] = field(default_factory=list)
    summary: TrajectorySummary | None = None
    hint_state: dict[str, Any] = field(default_factory=dict)
    resume_user_input: str | None = None

    def to_history(self) -> list[dict[str, Any]]:
        return [step.dump() for step in self.steps]

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
            "steps": self.to_history(),
            "summary": self.summary.model_dump(mode="json") if self.summary else None,
            "hint_state": dict(self.hint_state),
            "resume_user_input": self.resume_user_input,
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
        for step_data in payload.get("steps", []):
            action = PlannerAction.model_validate(step_data["action"])
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
        trajectory.artifacts.update(payload.get("artifacts") or {})
        for src in payload.get("sources") or []:
            if isinstance(src, Mapping):
                trajectory.sources.append(dict(src))
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
                pending.append(f"retry {step.action.next_node or 'finish'}")
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


__all__ = ["Trajectory", "TrajectoryStep", "TrajectorySummary"]
