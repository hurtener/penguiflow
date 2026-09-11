"""Offline mining of safe trace summaries into advisory-skill candidates."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .control_plane import AdvisorySkillCandidate
from .evidence import EvidenceContext

_SAFE_EVIDENCE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


def _safe_evidence_value(value: Any, field_name: str) -> Any:
    """Validate redacted evidence before it can enter a drafting prompt."""

    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must be finite")
        return value
    if isinstance(value, str):
        if _SAFE_EVIDENCE_TOKEN.fullmatch(value) is None:
            raise ValueError(f"{field_name} must contain only safe identifiers")
        return value
    if isinstance(value, Mapping):
        safe_mapping: dict[str, Any] = {}
        for name, item in value.items():
            if not isinstance(name, str):
                raise ValueError(f"{field_name} keys must be strings")
            safe_name = _safe_evidence_value(name, f"{field_name} key")
            safe_mapping[safe_name] = _safe_evidence_value(
                item,
                f"{field_name}.{name}",
            )
        return safe_mapping
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_safe_evidence_value(item, field_name) for item in value)
    raise ValueError(f"{field_name} contains an unsupported value")


@dataclass(frozen=True, slots=True)
class TraceLearningRecord:
    """One pre-redacted trace summary considered for offline candidate mining."""

    trace_id: str
    context: EvidenceContext
    recorded_at: datetime
    successful: bool
    pattern_key: str
    safe_summary: str
    investigation_digest: str | None = None
    intent_class: str = "unknown"
    verified_success: bool = False
    safe_evidence: Mapping[str, Any] = field(default_factory=dict)

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
        object.__setattr__(self, "intent_class", _non_empty(self.intent_class, "intent_class"))
        object.__setattr__(
            self,
            "safe_evidence",
            _safe_evidence_value(self.safe_evidence, "safe_evidence"),
        )
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TraceCohorts:
    """Records for mining and an optional later cohort reserved for evaluation."""

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
    safe_evidence: tuple[Mapping[str, Any], ...] = ()


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
    """Optionally reserve the newest distinct traces before mining begins."""

    if held_out_count < 0:
        raise ValueError("held_out_count must be zero or greater")
    trace_ids = [record.trace_id for record in records]
    if len(trace_ids) != len(set(trace_ids)):
        raise ValueError("trace_id values must be unique")
    if len(records) <= held_out_count:
        raise ValueError("at least one trace must remain for mining")

    ordered = tuple(sorted(records, key=lambda record: (record.recorded_at, record.trace_id)))
    if held_out_count == 0:
        return TraceCohorts(mining_records=ordered, held_out_records=())

    return TraceCohorts(
        mining_records=ordered[:-held_out_count],
        held_out_records=ordered[-held_out_count:],
    )


class CandidateMiner:
    """Draft candidates only from repeated, independently verified safe summaries."""

    def __init__(self, *, minimum_successes: int, drafter: CandidateDrafter) -> None:
        if minimum_successes < 2:
            raise ValueError("minimum_successes must be at least 2")
        self._minimum_successes = minimum_successes
        self._drafter = drafter

    def mine(self, records: Sequence[TraceLearningRecord]) -> tuple[MinedCandidate, ...]:
        """Group matching successful records and draft one candidate per eligible pattern."""

        patterns = find_repeated_successful_patterns(
            records,
            minimum_successes=self._minimum_successes,
        )
        return tuple(candidate_from_pattern(pattern, self._drafter(pattern)) for pattern in patterns)


def find_repeated_successful_patterns(
    records: Sequence[TraceLearningRecord],
    *,
    minimum_successes: int,
) -> tuple[TracePattern, ...]:
    """Return repeated patterns backed only by independently verified successes."""

    if minimum_successes < 2:
        raise ValueError("minimum_successes must be at least 2")

    grouped: dict[tuple[str, str, str], list[TraceLearningRecord]] = {}
    for record in records:
        if not record.successful or not record.verified_success:
            continue
        key = (record.context.agent_id, record.context.deployment_digest, record.pattern_key)
        grouped.setdefault(key, []).append(record)

    patterns: list[TracePattern] = []
    for key in sorted(grouped):
        matching_records = sorted(
            grouped[key],
            key=lambda record: (record.recorded_at, record.trace_id),
        )
        if len(matching_records) < minimum_successes:
            continue
        patterns.append(
            TracePattern(
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
                safe_evidence=tuple(record.safe_evidence for record in matching_records if record.safe_evidence),
            )
        )
    return tuple(patterns)


def candidate_from_pattern(pattern: TracePattern, advisory_skill: str) -> MinedCandidate:
    """Create one deterministic candidate from a selected pattern and validated draft."""

    candidate = AdvisorySkillCandidate(
        candidate_id=_candidate_id(pattern),
        advisory_skill=_non_empty(advisory_skill, "drafted advisory skill"),
        source_trace_ids=pattern.source_trace_ids,
        source_investigation_digests=pattern.source_investigation_digests,
    )
    return MinedCandidate(candidate=candidate, pattern=pattern)


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
    "candidate_from_pattern",
    "find_repeated_successful_patterns",
    "reserve_later_held_out_cohort",
]
