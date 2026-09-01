"""Offline mining of safe trace summaries into advisory-skill candidates."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from .control_plane import AdvisorySkillCandidate
from .evidence import EvidenceContext


def _non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


@dataclass(frozen=True, slots=True)
class TraceLearningRecord:
    """One pre-redacted trace summary eligible for offline candidate mining."""

    trace_id: str
    context: EvidenceContext
    recorded_at: datetime
    successful: bool
    pattern_key: str
    safe_summary: str
    investigation_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", _non_empty(self.trace_id, "trace_id"))
        object.__setattr__(self, "pattern_key", _non_empty(self.pattern_key, "pattern_key"))
        object.__setattr__(self, "safe_summary", _non_empty(self.safe_summary, "safe_summary"))
        if self.investigation_digest is not None:
            object.__setattr__(
                self,
                "investigation_digest",
                _non_empty(self.investigation_digest, "investigation_digest"),
            )
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TraceCohorts:
    """Earlier records for mining and later records reserved for evaluation."""

    mining_records: tuple[TraceLearningRecord, ...]
    held_out_records: tuple[TraceLearningRecord, ...]


@dataclass(frozen=True, slots=True)
class TracePattern:
    """A repeated successful pattern from one pinned agent deployment."""

    agent_id: str
    deployment_digest: str
    pattern_key: str
    source_trace_ids: tuple[str, ...]
    source_investigation_digests: tuple[str, ...]
    safe_summaries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MinedCandidate:
    """A draft advisory candidate and the successful evidence pattern behind it."""

    candidate: AdvisorySkillCandidate
    pattern: TracePattern


CandidateDrafter = Callable[[TracePattern], str]


def reserve_later_held_out_cohort(
    records: Sequence[TraceLearningRecord],
    *,
    held_out_count: int,
) -> TraceCohorts:
    """Reserve the newest distinct traces for evaluation before any mining begins."""

    if held_out_count < 1:
        raise ValueError("held_out_count must be at least 1")
    trace_ids = [record.trace_id for record in records]
    if len(trace_ids) != len(set(trace_ids)):
        raise ValueError("trace_id values must be unique")
    if len(records) <= held_out_count:
        raise ValueError("at least one trace must remain for mining")

    ordered = tuple(sorted(records, key=lambda record: (record.recorded_at, record.trace_id)))
    return TraceCohorts(
        mining_records=ordered[:-held_out_count],
        held_out_records=ordered[-held_out_count:],
    )


class CandidateMiner:
    """Draft advisory candidates only from repeated successful safe summaries."""

    def __init__(self, *, minimum_successes: int, drafter: CandidateDrafter) -> None:
        if minimum_successes < 2:
            raise ValueError("minimum_successes must be at least 2")
        self._minimum_successes = minimum_successes
        self._drafter = drafter

    def mine(self, records: Sequence[TraceLearningRecord]) -> tuple[MinedCandidate, ...]:
        """Group matching successful records and draft one candidate per eligible pattern."""

        grouped: dict[tuple[str, str, str], list[TraceLearningRecord]] = {}
        for record in records:
            if not record.successful:
                continue
            key = (record.context.agent_id, record.context.deployment_digest, record.pattern_key)
            grouped.setdefault(key, []).append(record)

        candidates: list[MinedCandidate] = []
        for key in sorted(grouped):
            matching_records = sorted(grouped[key], key=lambda record: (record.recorded_at, record.trace_id))
            if len(matching_records) < self._minimum_successes:
                continue
            pattern = TracePattern(
                agent_id=key[0],
                deployment_digest=key[1],
                pattern_key=key[2],
                source_trace_ids=tuple(record.trace_id for record in matching_records),
                source_investigation_digests=tuple(
                    record.investigation_digest
                    for record in matching_records
                    if record.investigation_digest is not None
                ),
                safe_summaries=tuple(record.safe_summary for record in matching_records),
            )
            advisory_skill = _non_empty(self._drafter(pattern), "drafted advisory skill")
            candidate = AdvisorySkillCandidate(
                candidate_id=_candidate_id(pattern),
                advisory_skill=advisory_skill,
                source_trace_ids=pattern.source_trace_ids,
                source_investigation_digests=pattern.source_investigation_digests,
            )
            candidates.append(MinedCandidate(candidate=candidate, pattern=pattern))
        return tuple(candidates)


def _candidate_id(pattern: TracePattern) -> str:
    source = "|".join((pattern.agent_id, pattern.deployment_digest, pattern.pattern_key, *pattern.source_trace_ids))
    return f"candidate_{hashlib.sha256(source.encode()).hexdigest()[:16]}"


__all__ = [
    "CandidateDrafter",
    "CandidateMiner",
    "MinedCandidate",
    "TraceCohorts",
    "TraceLearningRecord",
    "TracePattern",
    "reserve_later_held_out_cohort",
]
