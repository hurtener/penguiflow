from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from learning_control_plane.evidence import EvidenceContext
from learning_control_plane.mining import (
    CandidateMiner,
    TraceLearningRecord,
    candidate_from_pattern,
    find_repeated_successful_patterns,
    reserve_later_held_out_cohort,
)


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
        verified_success=successful,
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


def test_cohort_reservation_uses_every_trace_when_no_reserve_is_requested() -> None:
    records = tuple(_record(f"trace-{index}", index) for index in range(3))

    cohorts = reserve_later_held_out_cohort(records, held_out_count=0)

    assert tuple(record.trace_id for record in cohorts.mining_records) == ("trace-0", "trace-1", "trace-2")
    assert cohorts.held_out_records == ()


def test_miner_ignores_failures_and_patterns_below_the_minimum() -> None:
    records = (
        _record("trace-1", 1),
        _record("trace-2", 2, successful=False),
        _record("trace-3", 3, pattern_key="account-access"),
    )

    candidates = CandidateMiner(minimum_successes=2, drafter=lambda pattern: "Use verified steps.").mine(records)

    assert candidates == ()


def test_miner_ignores_completed_records_without_verified_outcomes() -> None:
    records = (
        _record("trace-1", 1),
        TraceLearningRecord(
            trace_id="trace-2",
            context=EvidenceContext(agent_id="support-agent", deployment_digest="sha256:bundle"),
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=2),
            successful=True,
            verified_success=False,
            pattern_key="billing-refund",
            safe_summary="Run completed, but its answer was not verified.",
        ),
    )

    candidates = CandidateMiner(minimum_successes=2, drafter=lambda pattern: "Use verified steps.").mine(records)

    assert candidates == ()


def test_learning_record_rejects_free_text_in_safe_evidence() -> None:
    with pytest.raises(ValueError, match="safe identifiers"):
        TraceLearningRecord(
            trace_id="trace-1",
            context=EvidenceContext(
                agent_id="support-agent",
                deployment_digest="sha256:bundle",
            ),
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
            successful=True,
            pattern_key="billing-refund",
            safe_summary="Verified refund status.",
            verified_success=True,
            safe_evidence={"raw_answer": "private customer answer"},
        )


def test_pattern_discovery_and_candidate_creation_can_run_as_separate_phases() -> None:
    records = (_record("trace-1", 1), _record("trace-2", 2), _record("trace-3", 3))

    patterns = find_repeated_successful_patterns(records, minimum_successes=3)
    mined_candidate = candidate_from_pattern(patterns[0], "Verify the authoritative status first.")

    assert len(patterns) == 1
    assert mined_candidate.pattern.pattern_key == "billing-refund"
    assert mined_candidate.candidate.advisory_skill == "Verify the authoritative status first."
    assert mined_candidate.candidate.source_trace_ids == ("trace-1", "trace-2", "trace-3")


def test_cohort_reservation_rejects_duplicate_trace_ids() -> None:
    records = (_record("trace-1", 1), _record("trace-1", 2), _record("trace-3", 3))

    with pytest.raises(ValueError, match="unique"):
        reserve_later_held_out_cohort(records, held_out_count=1)
