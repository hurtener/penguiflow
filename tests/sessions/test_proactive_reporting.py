"""Unit tests for proactive background task reporting.

These tests avoid running a full ReactPlanner loop by using small fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from typing_extensions import override

from penguiflow.sessions.models import (
    ContextPatch,
    MergeStrategy,
    ProactiveReportRequest,
    StateUpdate,
)
from penguiflow.sessions.proactive import (
    ProactivePlanner,
    create_default_proactive_generator,
    setup_proactive_reporting,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass
class _FakeSession:
    published: list[StateUpdate]
    consumed: list[str]
    configured: dict[str, object] | None = None

    def publish(self, update: StateUpdate) -> None:
        self.published.append(update)

    async def mark_background_consumed(self, *, task_ids: list[str]) -> int:
        self.consumed.extend(task_ids)
        return len(task_ids)

    def configure_proactive_reporting(self, **kwargs: object) -> None:
        self.configured = dict(kwargs)


class _FakePlanner(ProactivePlanner):
    @override
    async def run(
        self,
        query: str,
        *,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(payload={"answer": "proactive ok"})


class _FakePlannerAttrPayload(ProactivePlanner):
    @override
    async def run(
        self,
        query: str,
        *,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(payload=SimpleNamespace(answer="attr ok"))


class _FakePlannerStringPayload(ProactivePlanner):
    @override
    async def run(
        self,
        query: str,
        *,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(payload="string ok")


@pytest.mark.asyncio
async def test_proactive_generator_publishes_and_consumes() -> None:
    """Proactive generator publishes result and consumes background."""
    session = _FakeSession(published=[], consumed=[])

    patch = ContextPatch(
        task_id="t1",
        digest=["did thing"],
        facts={"k": "v"},
        artifacts=[
            {"id": "a1", "mime_type": "text/plain", "filename": "note.txt"}
        ],
    )
    req = ProactiveReportRequest(
        task_id="t1",
        session_id="s1",
        trace_id="trace1",
        task_description="do thing",
        execution_time_ms=123,
        patch=patch,
        merge_strategy=MergeStrategy.APPEND,
        proactive_hops_remaining=1,
    )

    gen = create_default_proactive_generator(
        lambda: _FakePlanner(), cast("Any", session), max_hops=2
    )
    await gen(req)

    assert len(session.published) == 1
    update = session.published[0]
    assert update.update_type.value == "RESULT"
    content = update.content
    assert content["proactive"] is True
    assert content["background_task_id"] == "t1"
    assert content["text"] == "proactive ok"
    assert session.consumed == ["t1"]


def test_setup_proactive_reporting_configures_session() -> None:
    """Setup proactive reporting configures session."""
    session = _FakeSession(published=[], consumed=[])

    setup_proactive_reporting(
        cast("Any", session),
        planner_factory=lambda: _FakePlanner(),
        enabled=True,
        strategies=["APPEND"],
        max_queued=3,
        timeout_s=1.0,
        max_hops=2,
        fallback_notification=False,
    )
    assert session.configured is not None
    assert session.configured["enabled"] is True
    assert session.configured["strategies"] == ["APPEND"]
    assert session.configured["max_queued"] == 3
    assert session.configured["timeout_s"] == 1.0
    assert session.configured["max_hops"] == 2
    assert session.configured["fallback_notification"] is False


@pytest.mark.asyncio
async def test_proactive_generator_noop_when_hops_exhausted() -> None:
    """Proactive generator noop when hops exhausted."""
    session = _FakeSession(published=[], consumed=[])
    patch = ContextPatch(task_id="t0", digest=["done"], facts={})
    req = ProactiveReportRequest(
        task_id="t0",
        session_id="s0",
        patch=patch,
        merge_strategy=MergeStrategy.APPEND,
        proactive_hops_remaining=0,
    )
    gen = create_default_proactive_generator(
        lambda: _FakePlanner(), cast("Any", session), max_hops=2
    )
    await gen(req)
    assert session.published == []
    assert session.consumed == []


def test_setup_proactive_reporting_disabled_is_noop() -> None:
    """Setup proactive reporting disabled is noop."""
    session = _FakeSession(published=[], consumed=[])
    setup_proactive_reporting(
        cast("Any", session),
        planner_factory=lambda: _FakePlanner(),
        enabled=False,
    )
    assert session.configured is None


@pytest.mark.asyncio
async def test_proactive_generator_group_report_and_nested_artifacts() -> None:
    """Proactive generator group report and nested artifacts."""
    session = _FakeSession(published=[], consumed=[])

    patch = ContextPatch(
        task_id="t2",
        digest=["main"],
        facts={"x": 1},
        artifacts=[
            {
                "artifact": {
                    "artifact": {
                        "id": "a1",
                        "mime_type": "text/plain",
                        "size_bytes": 1,
                        "filename": "a.txt",
                        "source": "not-a-mapping",
                    }
                }
            }
        ],
        recommended_next_steps=["next"],
    )
    combined = ContextPatch(
        task_id="t3",
        digest=["secondary"],
        facts={"y": 2},
        artifacts=[{"id": "a1"}],
    )
    req = ProactiveReportRequest(
        task_id="t2",
        session_id="s2",
        patch=patch,
        merge_strategy=MergeStrategy.APPEND,
        is_group_report=True,
        group_task_ids=["t2", "t3"],
        combined_patches=[combined],
    )

    bundle = SimpleNamespace(planner=_FakePlannerAttrPayload())
    gen = create_default_proactive_generator(
        lambda: bundle, cast("Any", session), max_hops=2
    )
    await gen(req)

    assert len(session.published) == 1
    update = session.published[0]
    assert update.content["proactive"] is True
    assert update.content["text"] == "attr ok"
    # Group reports acknowledge all tasks.
    assert sorted(session.consumed) == ["t2", "t3"]


@pytest.mark.asyncio
async def test_proactive_generator_default_summary_and_payload_string() -> (
    None
):
    """Proactive generator default summary and payload string."""
    session = _FakeSession(published=[], consumed=[])

    patch = ContextPatch(
        task_id="t4",
        digest=[],
        facts={},
        artifacts=[{"artifact": {"id": "a2", "source": {"kind": "test"}}}],
        recommended_next_steps=["follow_up"],
    )
    req = ProactiveReportRequest(
        task_id="t4",
        session_id="s4",
        patch=patch,
        merge_strategy=MergeStrategy.APPEND,
        proactive_hops_remaining=None,
    )

    bundle = SimpleNamespace(planner=_FakePlannerStringPayload())
    gen = create_default_proactive_generator(
        lambda: bundle, cast("Any", session), max_hops=1
    )
    await gen(req)

    assert len(session.published) == 1
    assert session.published[0].content["text"] == "string ok"


class _FixedPayloadPlanner(ProactivePlanner):
    """Planner fake whose run result carries a fixed payload."""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, object, object]] = []

    @override
    async def run(
        self,
        query: str,
        *,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
    ) -> SimpleNamespace:
        """Record the call and return the fixed payload."""
        self.calls.append((query, llm_context, tool_context))
        return SimpleNamespace(payload=self.payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, SimpleNamespace(foo="bar")])
async def test_proactive_generator_defaults_when_payload_has_no_answer(
    payload: object,
) -> None:
    """No answer and no usable artifact refs publish the defaults."""
    session = _FakeSession(published=[], consumed=[])
    patch = ContextPatch(task_id="t5", artifacts=[{"id": ""}, {}])
    req = ProactiveReportRequest(
        task_id="t5",
        session_id="s5",
        patch=patch,
        merge_strategy=MergeStrategy.APPEND,
    )
    planner = _FixedPayloadPlanner(payload)
    gen = create_default_proactive_generator(
        lambda: planner, cast("Any", session)
    )
    await gen(req)

    content = session.published[0].content
    assert content["text"] == "Background task completed."
    assert content["artifacts"] is None
