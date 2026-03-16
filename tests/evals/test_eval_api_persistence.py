from __future__ import annotations

import asyncio
from typing import Any

import pytest

from penguiflow.evals.api import (
    _EvalOrchestratorWrapper,
    _EvalPlannerWrapper,
    _build_discovered_run_one,
    collect_traces,
)
from penguiflow.planner import PlannerFinish, Trajectory


class _StoreWithDelayedSave:
    def __init__(self) -> None:
        self._trajectories: dict[tuple[str, str], Trajectory] = {}

    async def save_trajectory(self, trace_id: str, session_id: str, trajectory: Trajectory) -> None:
        self._trajectories[(trace_id, session_id)] = trajectory

    async def get_trajectory(self, trace_id: str, session_id: str) -> Trajectory | None:
        return self._trajectories.get((trace_id, session_id))

    async def list_planner_events(self, trace_id: str) -> list[Any]:
        del trace_id
        return []


@pytest.mark.asyncio
async def test_discovered_run_one_waits_for_trace_persistence_before_reading_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _StoreWithDelayedSave()

    class _Runner:
        def __init__(self) -> None:
            self.wait_calls = 0
            self._persist_task: asyncio.Task[None] | None = None

        async def chat(
            self,
            query: str,
            *,
            session_id: str,
            llm_context: dict[str, Any] | None = None,
            tool_context: dict[str, Any] | None = None,
        ) -> Any:
            del llm_context, tool_context
            trace_id = "trace-delayed"
            trajectory = Trajectory.from_serialised(
                {
                    "query": query,
                    "llm_context": {},
                    "tool_context": {"session_id": session_id, "trace_id": trace_id},
                    "steps": [{"action": {"next_node": "triage_query", "args": {}}}],
                }
            )

            async def _persist() -> None:
                await asyncio.sleep(0.05)
                await store.save_trajectory(trace_id, session_id, trajectory)

            self._persist_task = asyncio.create_task(_persist())
            return type(
                "_Result",
                (),
                {"answer": "ok", "trace_id": trace_id, "session_id": session_id, "metadata": {}},
            )()

        async def wait_for_trace_persistence(
            self,
            trace_id: str,
            session_id: str,
            *,
            timeout_s: float = 1.0,
        ) -> None:
            del trace_id, session_id
            self.wait_calls += 1
            if self._persist_task is not None:
                await asyncio.wait_for(self._persist_task, timeout=timeout_s)

    runner = _Runner()

    async def _build_runner(*, project_root: str, state_store: Any, agent_package: str | None = None) -> Any:
        del project_root, state_store, agent_package
        return runner

    monkeypatch.setattr("penguiflow.evals.api._build_project_runner", _build_runner)

    run_one = await _build_discovered_run_one(
        project_root=".",
        state_store=store,
        prediction_session_id="eval-session",
    )

    pred, pred_trace = await run_one({"question": "what is policy", "gold_trace": {}}, None)

    assert pred == "ok"
    assert runner.wait_calls == 1
    assert isinstance(pred_trace, dict)
    assert isinstance(pred_trace.get("steps"), list)
    assert pred_trace["steps"][0]["action"]["next_node"] == "triage_query"


@pytest.mark.asyncio
async def test_collect_traces_waits_for_persistence_before_tagging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _StoreWithDelayedSave()

    class _Runner:
        def __init__(self) -> None:
            self._persist_task: asyncio.Task[None] | None = None
            self.wait_calls = 0

        async def chat(
            self,
            query: str,
            *,
            session_id: str,
            llm_context: dict[str, Any] | None = None,
            tool_context: dict[str, Any] | None = None,
        ) -> Any:
            del llm_context, tool_context
            trace_id = "trace-collect"
            trajectory = Trajectory.from_serialised(
                {
                    "query": query,
                    "llm_context": {},
                    "tool_context": {"session_id": session_id, "trace_id": trace_id},
                    "steps": [{"action": {"next_node": "triage_query", "args": {}}}],
                    "metadata": {"tags": ["existing"]},
                }
            )

            async def _persist() -> None:
                await asyncio.sleep(0.05)
                await store.save_trajectory(trace_id, session_id, trajectory)

            self._persist_task = asyncio.create_task(_persist())
            return type(
                "_Result",
                (),
                {"answer": "ok", "trace_id": trace_id, "session_id": session_id, "metadata": {}},
            )()

        async def wait_for_trace_persistence(
            self,
            trace_id: str,
            session_id: str,
            *,
            timeout_s: float = 1.0,
        ) -> None:
            del trace_id, session_id
            self.wait_calls += 1
            if self._persist_task is not None:
                await asyncio.wait_for(self._persist_task, timeout=timeout_s)

    runner = _Runner()

    async def _build_runner(*, project_root: str, state_store: Any, agent_package: str | None = None) -> Any:
        del project_root, state_store, agent_package
        return runner

    monkeypatch.setattr("penguiflow.evals.api._build_project_runner", _build_runner)

    result = await collect_traces(
        project_root=".",
        state_store=store,
        session_id="eval-collect-session",
        queries=[{"query": "collect one", "split": "val", "tags": ["dataset:policy-v1"]}],
    )

    saved = await store.get_trajectory("trace-collect", "eval-collect-session")
    assert result["trace_count"] == 1
    assert runner.wait_calls == 1
    assert saved is not None
    assert saved.metadata is not None
    tags = saved.metadata.get("tags", [])
    assert "existing" in tags
    assert "dataset:policy-v1" in tags
    assert "split:val" in tags


@pytest.mark.asyncio
async def test_eval_planner_wrapper_does_not_manually_save_trajectory_anymore() -> None:
    class _Planner:
        async def run(self, **_: Any) -> PlannerFinish:
            return PlannerFinish(
                reason="answer_complete",
                payload={"answer": "ok"},
                metadata={"steps": [{"action": {"next_node": "triage_query", "args": {}}}]},
            )

        async def wait_for_trace_persistence(
            self,
            trace_id: str,
            *,
            session_id: str | None = None,
            timeout_s: float = 1.0,
        ) -> bool:
            del trace_id, session_id, timeout_s
            return True

    class _Store:
        def __init__(self) -> None:
            self.save_calls = 0

        async def save_trajectory(self, trace_id: str, session_id: str, trajectory: Trajectory) -> None:
            del trace_id, session_id, trajectory
            self.save_calls += 1

    store = _Store()
    wrapper = _EvalPlannerWrapper(_Planner(), state_store=store)
    result = await wrapper.chat("q", session_id="s")

    assert result.answer == "ok"
    assert store.save_calls == 0


@pytest.mark.asyncio
async def test_eval_orchestrator_wrapper_overrides_stale_tool_context_ids() -> None:
    class _Orchestrator:
        def __init__(self) -> None:
            self.last_tool_context: dict[str, Any] | None = None

        async def execute(
            self,
            *,
            query: str,
            tenant_id: str,
            user_id: str,
            session_id: str,
            tool_context: dict[str, Any],
        ) -> dict[str, Any]:
            del query, tenant_id, user_id, session_id
            self.last_tool_context = dict(tool_context)
            return {
                "answer": "ok",
                "trace_id": str(tool_context.get("trace_id") or ""),
                "metadata": {},
            }

    orchestrator = _Orchestrator()
    wrapper = _EvalOrchestratorWrapper(orchestrator)
    result = await wrapper.chat(
        "q",
        session_id="eval-dataset-pred",
        tool_context={
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "session_id": "old-collected-session",
            "trace_id": "old-collected-trace",
        },
    )

    assert result.answer == "ok"
    assert orchestrator.last_tool_context is not None
    assert orchestrator.last_tool_context["session_id"] == "eval-dataset-pred"
    assert orchestrator.last_tool_context["trace_id"] != "old-collected-trace"


@pytest.mark.asyncio
async def test_eval_orchestrator_wrapper_forwards_memories_from_llm_context() -> None:
    class _Orchestrator:
        def __init__(self) -> None:
            self.last_memories: list[dict[str, Any]] | None = None

        async def execute(
            self,
            *,
            query: str,
            tenant_id: str,
            user_id: str,
            session_id: str,
            tool_context: dict[str, Any],
            memories: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            del query, tenant_id, user_id, session_id, tool_context
            self.last_memories = memories
            return {"answer": "ok", "trace_id": "trace-1", "metadata": {}}

    orchestrator = _Orchestrator()
    wrapper = _EvalOrchestratorWrapper(orchestrator)
    memories = [{"role": "user", "content": "remember deployment 2.3.1"}]

    result = await wrapper.chat(
        "q",
        session_id="eval-dataset-pred",
        llm_context={"memories": memories},
        tool_context={"tenant_id": "tenant-1", "user_id": "user-1"},
    )

    assert result.answer == "ok"
    assert orchestrator.last_memories == memories
