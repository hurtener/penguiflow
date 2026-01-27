"""Unit tests for proactive background task reporting.

These tests avoid running a full ReactPlanner loop by using small fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest

from penguiflow.sessions.models import ContextPatch, MergeStrategy, ProactiveReportRequest, StateUpdate
from penguiflow.sessions.proactive import create_default_proactive_generator, setup_proactive_reporting


@dataclass
class _FakeSession:
    published: list[StateUpdate]
    consumed: list[str]
    configured: dict[str, object] | None = None

    def _publish(self, update):
        self.published.append(update)

    async def mark_background_consumed(self, *, task_ids: list[str]) -> int:
        self.consumed.extend(task_ids)
        return len(task_ids)

    def configure_proactive_reporting(self, **kwargs):
        self.configured = dict(kwargs)


class _FakePlanner:
    async def run(self, *, query: str, llm_context: dict, tool_context: dict):
        _ = (query, llm_context, tool_context)
        return SimpleNamespace(payload={"answer": "proactive ok"})


class _FakePlannerAttrPayload:
    async def run(self, *, query: str, llm_context: dict, tool_context: dict):
        _ = (query, llm_context, tool_context)
        return SimpleNamespace(payload=SimpleNamespace(answer="attr ok"))


class _FakePlannerStringPayload:
    async def run(self, *, query: str, llm_context: dict, tool_context: dict):
        _ = (query, llm_context, tool_context)
        return SimpleNamespace(payload="string ok")


@pytest.mark.asyncio
async def test_proactive_generator_publishes_result_and_consumes_background() -> None:
    session = _FakeSession(published=[], consumed=[])

    patch = ContextPatch(
        task_id="t1",
        digest=["did thing"],
        facts={"k": "v"},
        artifacts=[{"id": "a1", "mime_type": "text/plain", "filename": "note.txt"}],
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

    gen = create_default_proactive_generator(lambda: _FakePlanner(), cast(Any, session), max_hops=2)
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
    session = _FakeSession(published=[], consumed=[])

    setup_proactive_reporting(
        cast(Any, session),
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
    session = _FakeSession(published=[], consumed=[])
    patch = ContextPatch(task_id="t0", digest=["done"], facts={})
    req = ProactiveReportRequest(
        task_id="t0",
        session_id="s0",
        patch=patch,
        merge_strategy=MergeStrategy.APPEND,
        proactive_hops_remaining=0,
    )
    gen = create_default_proactive_generator(lambda: _FakePlanner(), cast(Any, session), max_hops=2)
    await gen(req)
    assert session.published == []
    assert session.consumed == []


def test_setup_proactive_reporting_disabled_is_noop() -> None:
    session = _FakeSession(published=[], consumed=[])
    setup_proactive_reporting(
        cast(Any, session),
        planner_factory=lambda: _FakePlanner(),
        enabled=False,
    )
    assert session.configured is None


@pytest.mark.asyncio
async def test_proactive_generator_group_report_and_nested_artifacts() -> None:
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
    gen = create_default_proactive_generator(lambda: bundle, cast(Any, session), max_hops=2)
    await gen(req)

    assert len(session.published) == 1
    update = session.published[0]
    assert update.content["proactive"] is True
    assert update.content["text"] == "attr ok"
    # Group reports acknowledge all tasks.
    assert sorted(session.consumed) == ["t2", "t3"]


@pytest.mark.asyncio
async def test_proactive_generator_default_summary_and_payload_string() -> None:
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
    gen = create_default_proactive_generator(lambda: bundle, cast(Any, session), max_hops=1)
    await gen(req)

    assert len(session.published) == 1
    assert session.published[0].content["text"] == "string ok"


def test_proactive_helpers_cover_edge_cases() -> None:
    import penguiflow.sessions.proactive as proactive_mod

    assert proactive_mod._extract_answer(None) is None
    assert proactive_mod._extract_answer(SimpleNamespace(foo="bar")) is None
    assert proactive_mod._extract_artifact_ref("not-a-mapping") is None
    assert proactive_mod._normalize_artifact_ref({"id": ""}) is None

    # Cover ref=None path inside _collect_artifact_refs.
    patch = ContextPatch(task_id="t5", artifacts=[{}])
    req = ProactiveReportRequest(
        task_id="t5",
        session_id="s5",
        patch=patch,
        merge_strategy=MergeStrategy.APPEND,
    )
    assert proactive_mod._collect_artifact_refs(req) == []
