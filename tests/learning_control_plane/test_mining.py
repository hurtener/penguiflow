from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from learning_control_plane.evidence import EvidenceContext
from learning_control_plane.mining import CandidateMiner, TraceLearningRecord, reserve_later_held_out_cohort


def _record(
    trace_id: str,
    minute: int,
    *,
    successful: bool = True,
    pattern_key: str = "billing-refund",
) -> TraceLearningRecord:
    return TraceLearningRecord(
        trace_id=trace_id,
        context=EvidenceContext(agent_id="support-agent", deployment_digest="sha256:bundle", trace_id=trace_id),
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        successful=successful,
        pattern_key=pattern_key,
        safe_summary="Verified refund status before responding.",
    )


def test_miner_drafts_from_repeated_successes_but_not_the_held_out_cohort() -> None:
    records = tuple(_record(f"trace-{index}", index) for index in range(5))
    cohorts = reserve_later_held_out_cohort(records, held_out_count=2)

    candidates = CandidateMiner(
        minimum_successes=3,
        drafter=lambda pattern: f"For {pattern.pattern_key}, verify the authoritative status first.",
    ).mine(cohorts.mining_records)

    assert len(candidates) == 1
    assert candidates[0].candidate.source_trace_ids == ("trace-0", "trace-1", "trace-2")
    assert tuple(record.trace_id for record in cohorts.held_out_records) == ("trace-3", "trace-4")


def test_miner_ignores_failures_and_patterns_below_the_minimum() -> None:
    records = (
        _record("trace-1", 1),
        _record("trace-2", 2, successful=False),
        _record("trace-3", 3, pattern_key="account-access"),
    )

    candidates = CandidateMiner(minimum_successes=2, drafter=lambda pattern: "Use verified steps.").mine(records)

    assert candidates == ()


def test_cohort_reservation_rejects_duplicate_trace_ids() -> None:
    records = (_record("trace-1", 1), _record("trace-1", 2), _record("trace-3", 3))

    with pytest.raises(ValueError, match="unique"):
        reserve_later_held_out_cohort(records, held_out_count=1)
