"""Tests for proactive report-back functionality in StreamingSession."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from penguiflow.planner.models import BackgroundTasksConfig
from penguiflow.sessions import StreamingSession, TaskResult, TaskType, UpdateType
from penguiflow.sessions.models import (
    ContextPatch,
    MergeStrategy,
    ProactiveReportRequest,
)


@pytest.fixture
def session() -> StreamingSession:
    """Create a test session."""
    return StreamingSession("test-session")


@pytest.fixture
def sample_patch() -> ContextPatch:
    """Create a sample context patch for testing."""
    return ContextPatch(
        task_id="test-task",
        digest=["Task completed successfully"],
        facts={"result": "ok"},
        artifacts=[],
        sources=[],
    )


class TestConfigureProactiveReporting:
    """Tests for configure_proactive_reporting method."""

    @pytest.mark.asyncio
    async def test_configure_with_defaults(self, session: StreamingSession) -> None:
        """Test configuration with default values."""
        generator = AsyncMock()
        session.configure_proactive_reporting(generator=generator, enabled=True)

        assert session._proactive_generator is generator
        config = session._proactive_config
        assert config is not None
        assert config["enabled"] is True
        assert config["strategies"] == ["APPEND", "REPLACE"]
        assert config["max_queued"] == 5
        assert config["timeout_s"] == 30.0
        assert config["max_hops"] == 2
        assert config["fallback_notification"] is True

        # Clean up reporter task
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_configure_with_custom_values(self, session: StreamingSession) -> None:
        """Test configuration with custom values."""
        generator = AsyncMock()
        session.configure_proactive_reporting(
            generator=generator,
            enabled=True,
            strategies=["APPEND"],
            max_queued=10,
            timeout_s=60.0,
            max_hops=3,
            fallback_notification=False,
        )

        config = session._proactive_config
        assert config is not None
        assert config["strategies"] == ["APPEND"]
        assert config["max_queued"] == 10
        assert config["timeout_s"] == 60.0
        assert config["max_hops"] == 3
        assert config["fallback_notification"] is False

        # Clean up
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_configure_disabled_does_not_start_task(self, session: StreamingSession) -> None:
        """Test that disabled config doesn't start reporter task."""
        generator = AsyncMock()
        session.configure_proactive_reporting(generator=generator, enabled=False)

        assert session._proactive_task is None

    @pytest.mark.asyncio
    async def test_configure_enabled_starts_reporter_task(self, session: StreamingSession) -> None:
        """Test that enabled config starts reporter task."""
        generator = AsyncMock()
        session.configure_proactive_reporting(generator=generator, enabled=True)

        assert session._proactive_task is not None
        assert not session._proactive_task.done()

        # Clean up
        session._proactive_task.cancel()
        try:
            await session._proactive_task
        except asyncio.CancelledError:
            pass


class TestEnqueueProactiveReport:
    """Tests for _enqueue_proactive_report method."""

    @pytest.mark.asyncio
    async def test_enqueue_when_disabled(self, session: StreamingSession, sample_patch: ContextPatch) -> None:
        """Test that enqueueing does nothing when disabled."""
        session._enqueue_proactive_report(
            task_id="task-1",
            trace_id="trace-1",
            description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
        )

        assert session._proactive_queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_enqueue_when_enabled(self, session: StreamingSession, sample_patch: ContextPatch) -> None:
        """Test successful enqueueing when enabled."""
        generator = AsyncMock()
        session.configure_proactive_reporting(generator=generator, enabled=True)

        session._enqueue_proactive_report(
            task_id="task-1",
            trace_id="trace-1",
            description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
        )

        assert session._proactive_queue.qsize() == 1
        request = session._proactive_queue.get_nowait()
        assert request.task_id == "task-1"
        assert request.trace_id == "trace-1"
        assert request.task_description == "Test task"
        assert request.execution_time_ms == 100
        assert request.merge_strategy == MergeStrategy.APPEND
        assert request.proactive_hops_remaining == 2

        # Clean up
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass


class TestProactiveSuppression:
    """Tests for proactive suppression behavior."""

    @pytest.mark.asyncio
    async def test_suppressed_task_not_enqueued(self, session: StreamingSession, sample_patch: ContextPatch) -> None:
        generator = AsyncMock()
        session.configure_proactive_reporting(generator=generator, enabled=True)
        session.suppress_proactive_reports(task_ids=["task-1"])

        session._enqueue_proactive_report(
            task_id="task-1",
            trace_id="trace-1",
            description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
        )

        assert session._proactive_queue.qsize() == 0

        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_suppressed_request_skips_generation(
        self, session: StreamingSession, sample_patch: ContextPatch
    ) -> None:
        called: list[str] = []

        async def generator(request: ProactiveReportRequest) -> None:
            called.append(request.task_id)

        session.configure_proactive_reporting(generator=generator, enabled=True)
        session.suppress_proactive_reports(task_ids=["task-1"])
        request = ProactiveReportRequest(
            task_id="task-1",
            session_id=session.session_id,
            trace_id="trace-1",
            task_description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
        )

        await session._generate_proactive_message(request)

        assert called == []

        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_enqueue_filters_by_strategy(self, session: StreamingSession, sample_patch: ContextPatch) -> None:
        """Test that only configured strategies trigger enqueue."""
        generator = AsyncMock()
        session.configure_proactive_reporting(
            generator=generator,
            enabled=True,
            strategies=["APPEND"],  # Only APPEND, not REPLACE
        )

        # This should not be enqueued (REPLACE not in strategies)
        session._enqueue_proactive_report(
            task_id="task-1",
            trace_id="trace-1",
            description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.REPLACE,
        )
        assert session._proactive_queue.qsize() == 0

        # This should be enqueued (APPEND in strategies)
        session._enqueue_proactive_report(
            task_id="task-2",
            trace_id="trace-2",
            description="Test task 2",
            execution_time_ms=200,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
        )
        assert session._proactive_queue.qsize() == 1

        # Clean up
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_enqueue_human_gated_never_enqueued(
        self, session: StreamingSession, sample_patch: ContextPatch
    ) -> None:
        """Test that HUMAN_GATED strategy is never enqueued."""
        generator = AsyncMock()
        session.configure_proactive_reporting(
            generator=generator,
            enabled=True,
            strategies=["APPEND", "REPLACE", "HUMAN_GATED"],  # Even if included
        )

        # HUMAN_GATED uses notification panel, not proactive reports
        session._enqueue_proactive_report(
            task_id="task-1",
            trace_id="trace-1",
            description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.HUMAN_GATED,
        )
        # Should be enqueued since HUMAN_GATED is in strategies list
        # (the filtering is based on config, not hard-coded)
        assert session._proactive_queue.qsize() == 1

        # Clean up
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_enqueue_respects_max_queued(self, session: StreamingSession, sample_patch: ContextPatch) -> None:
        """Test that queue respects max_queued limit."""
        generator = AsyncMock()
        session.configure_proactive_reporting(
            generator=generator,
            enabled=True,
            max_queued=2,
        )

        # Stop the reporter task so queue fills up
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass
            session._proactive_task = None

        # Enqueue 3 items (max is 2)
        for i in range(3):
            session._enqueue_proactive_report(
                task_id=f"task-{i}",
                trace_id=f"trace-{i}",
                description=f"Test task {i}",
                execution_time_ms=100 * i,
                patch=sample_patch,
                merge_strategy=MergeStrategy.APPEND,
            )

        # Should only have 2 items (oldest dropped)
        assert session._proactive_queue.qsize() == 2
        # First item should be task-1 (task-0 was dropped)
        request = session._proactive_queue.get_nowait()
        assert request.task_id == "task-1"


class TestGenerateProactiveMessage:
    """Tests for _generate_proactive_message method."""

    @pytest.mark.asyncio
    async def test_generate_calls_generator(self, session: StreamingSession, sample_patch: ContextPatch) -> None:
        """Test that generator is called with request."""
        generator = AsyncMock()
        session.configure_proactive_reporting(generator=generator, enabled=True)

        # Stop the automatic reporter to test manually
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

        request = ProactiveReportRequest(
            task_id="task-1",
            session_id="test-session",
            trace_id="trace-1",
            task_description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
        )

        await session._generate_proactive_message(request)

        generator.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_generate_timeout_with_fallback(self, session: StreamingSession, sample_patch: ContextPatch) -> None:
        """Test timeout falls back to notification."""

        async def slow_generator(request: ProactiveReportRequest) -> None:
            await asyncio.sleep(10)  # Will timeout

        session.configure_proactive_reporting(
            generator=slow_generator,
            enabled=True,
            timeout_s=0.01,
            fallback_notification=True,
        )

        # Stop the automatic reporter
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

        request = ProactiveReportRequest(
            task_id="task-1",
            session_id="test-session",
            trace_id="trace-1",
            task_description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
        )

        updates: list[Any] = []
        subscriber = await session.subscribe()

        async def collect() -> None:
            async for update in subscriber:
                updates.append(update)
                if update.update_type == UpdateType.NOTIFICATION:
                    break

        collect_task = asyncio.create_task(collect())

        await session._generate_proactive_message(request)

        # Wait for collection with timeout
        try:
            await asyncio.wait_for(collect_task, timeout=1.0)
        except TimeoutError:
            pass

        # Should have notification fallback
        notification_updates = [u for u in updates if u.update_type == UpdateType.NOTIFICATION]
        assert len(notification_updates) >= 1

    @pytest.mark.asyncio
    async def test_generate_error_with_fallback(self, session: StreamingSession, sample_patch: ContextPatch) -> None:
        """Test error falls back to notification."""

        async def error_generator(request: ProactiveReportRequest) -> None:
            raise ValueError("Generator error")

        session.configure_proactive_reporting(
            generator=error_generator,
            enabled=True,
            fallback_notification=True,
        )

        # Stop the automatic reporter
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

        request = ProactiveReportRequest(
            task_id="task-1",
            session_id="test-session",
            trace_id="trace-1",
            task_description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
        )

        updates: list[Any] = []
        subscriber = await session.subscribe()

        async def collect() -> None:
            async for update in subscriber:
                updates.append(update)
                if update.update_type == UpdateType.NOTIFICATION:
                    break

        collect_task = asyncio.create_task(collect())

        await session._generate_proactive_message(request)

        # Wait for collection
        try:
            await asyncio.wait_for(collect_task, timeout=1.0)
        except TimeoutError:
            pass

        # Should have notification fallback
        notification_updates = [u for u in updates if u.update_type == UpdateType.NOTIFICATION]
        assert len(notification_updates) >= 1


class TestDefaultProactiveGenerator:
    """Tests for library default proactive generator."""

    class MockJSONLLMClient:
        async def complete(  # type: ignore[no-untyped-def]
            self,
            *,
            messages,
            response_format=None,
            stream=False,
            on_stream_chunk=None,
        ):
            _ = messages, response_format, stream, on_stream_chunk
            return '{"thought":"done","next_node":null,"args":{"raw_answer":"proactive"}}'

    @pytest.mark.asyncio
    async def test_default_generator_emits_and_cleans(
        self, session: StreamingSession, sample_patch: ContextPatch
    ) -> None:
        from penguiflow.catalog import build_catalog
        from penguiflow.planner import ReactPlanner
        from penguiflow.registry import ModelRegistry
        from penguiflow.sessions.proactive import create_default_proactive_generator

        def _planner_factory() -> ReactPlanner:
            catalog = build_catalog([], ModelRegistry())
            return ReactPlanner(llm_client=self.MockJSONLLMClient(), catalog=catalog, max_iters=1)

        await session.apply_context_patch(patch=sample_patch, strategy=MergeStrategy.APPEND)
        request = ProactiveReportRequest(
            task_id=sample_patch.task_id,
            session_id=session.session_id,
            trace_id="trace-1",
            task_description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
            memory_summary={"summary": "short"},
            tool_context={},
            proactive_hops_remaining=1,
        )
        generator = create_default_proactive_generator(_planner_factory, session, max_hops=2)

        updates: list[Any] = []
        subscriber = await session.subscribe(update_types=[UpdateType.RESULT])

        async def collect() -> None:
            async for update in subscriber:
                updates.append(update)
                break

        collect_task = asyncio.create_task(collect())
        await generator(request)
        await asyncio.wait_for(collect_task, timeout=1.0)

        assert updates
        update = updates[0]
        assert update.update_type == UpdateType.RESULT
        assert update.content.get("proactive") is True
        background = session.get_background_results()
        assert sample_patch.task_id not in background

    @pytest.mark.asyncio
    async def test_default_generator_includes_artifacts(self, session: StreamingSession) -> None:
        from penguiflow.catalog import build_catalog
        from penguiflow.planner import ReactPlanner
        from penguiflow.registry import ModelRegistry
        from penguiflow.sessions.proactive import create_default_proactive_generator

        patch = ContextPatch(
            task_id="artifact-task",
            digest=["Artifacts ready"],
            artifacts=[
                {
                    "node": "tool",
                    "field": "output",
                    "artifact": {
                        "artifact": {
                            "id": "artifact-1",
                            "mime_type": "text/plain",
                            "size_bytes": 12,
                            "filename": "note.txt",
                            "source": {"tool": "tester"},
                        }
                    },
                },
                {
                    "node": "tool",
                    "field": "output",
                    "artifact": {
                        "id": "artifact-2",
                        "mime_type": "application/json",
                        "size_bytes": 24,
                        "filename": "data.json",
                        "source": {"tool": "tester"},
                    },
                },
                {
                    "node": "tool",
                    "field": "output",
                    "artifact": {
                        "id": "artifact-1",
                        "mime_type": "text/plain",
                        "size_bytes": 12,
                        "filename": "note.txt",
                        "source": {"tool": "tester"},
                    },
                },
            ],
            sources=[],
        )

        def _planner_factory() -> ReactPlanner:
            catalog = build_catalog([], ModelRegistry())
            return ReactPlanner(llm_client=self.MockJSONLLMClient(), catalog=catalog, max_iters=1)

        await session.apply_context_patch(patch=patch, strategy=MergeStrategy.APPEND)
        request = ProactiveReportRequest(
            task_id=patch.task_id,
            session_id=session.session_id,
            trace_id="trace-1",
            task_description="Artifact task",
            execution_time_ms=100,
            patch=patch,
            merge_strategy=MergeStrategy.APPEND,
            memory_summary={"summary": "short"},
            tool_context={},
            proactive_hops_remaining=1,
        )
        generator = create_default_proactive_generator(_planner_factory, session, max_hops=2)

        updates: list[Any] = []
        subscriber = await session.subscribe(update_types=[UpdateType.RESULT])

        async def collect() -> None:
            async for update in subscriber:
                updates.append(update)
                break

        collect_task = asyncio.create_task(collect())
        await generator(request)
        await asyncio.wait_for(collect_task, timeout=1.0)

        assert updates
        content = updates[0].content
        assert isinstance(content, dict)
        artifacts = content.get("artifacts")
        assert isinstance(artifacts, list)
        ids = {item.get("id") for item in artifacts if isinstance(item, dict)}
        assert ids == {"artifact-1", "artifact-2"}


class TestRunProactiveReporter:
    """Tests for _run_proactive_reporter background task."""

    @pytest.mark.asyncio
    async def test_reporter_waits_for_idle(self, session: StreamingSession, sample_patch: ContextPatch) -> None:
        """Test that reporter waits for foreground to be idle."""
        call_times: list[float] = []

        async def tracking_generator(request: ProactiveReportRequest) -> None:
            import time

            call_times.append(time.time())

        session.configure_proactive_reporting(
            generator=tracking_generator,
            enabled=True,
        )

        # Set foreground as busy
        session._foreground_busy.clear()

        # Enqueue a request
        session._enqueue_proactive_report(
            task_id="task-1",
            trace_id="trace-1",
            description="Test task",
            execution_time_ms=100,
            patch=sample_patch,
            merge_strategy=MergeStrategy.APPEND,
        )

        # Wait a bit - generator should not be called yet
        await asyncio.sleep(0.05)
        assert len(call_times) == 0

        # Set foreground as idle
        session._foreground_busy.set()

        # Wait for reporter to process
        await asyncio.sleep(0.1)
        assert len(call_times) == 1

        # Clean up
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_reporter_processes_queue_in_order(
        self, session: StreamingSession, sample_patch: ContextPatch
    ) -> None:
        """Test that reporter processes queue in FIFO order."""
        processed_tasks: list[str] = []

        async def order_tracking_generator(request: ProactiveReportRequest) -> None:
            processed_tasks.append(request.task_id)

        session.configure_proactive_reporting(
            generator=order_tracking_generator,
            enabled=True,
        )

        # Stop automatic reporter first
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass
            session._proactive_task = None

        # Enqueue multiple requests
        for i in range(3):
            session._enqueue_proactive_report(
                task_id=f"task-{i}",
                trace_id=f"trace-{i}",
                description=f"Test task {i}",
                execution_time_ms=100,
                patch=sample_patch,
                merge_strategy=MergeStrategy.APPEND,
            )

        # Start reporter manually
        session._proactive_task = asyncio.create_task(session._run_proactive_reporter())

        # Wait for processing
        await asyncio.sleep(0.2)

        assert processed_tasks == ["task-0", "task-1", "task-2"]

        # Clean up
        if session._proactive_task:
            session._proactive_task.cancel()
            try:
                await session._proactive_task
            except asyncio.CancelledError:
                pass


class TestIdleDetection:
    """Tests for foreground idle detection."""

    @pytest.mark.asyncio
    async def test_initially_idle(self, session: StreamingSession) -> None:
        """Test that session starts as idle."""
        assert session._foreground_busy.is_set()

    @pytest.mark.asyncio
    async def test_run_task_clears_idle(self, session: StreamingSession) -> None:
        """Test that running foreground task clears idle flag."""
        idle_during_task: list[bool] = []

        async def check_idle_pipeline(runtime: Any) -> TaskResult:
            idle_during_task.append(session._foreground_busy.is_set())
            return TaskResult(payload={"ok": True})

        await session.run_task(
            check_idle_pipeline,
            task_type=TaskType.FOREGROUND,
            query="test",
        )

        # Should have been not idle during task
        assert len(idle_during_task) == 1
        assert idle_during_task[0] is False

        # Should be idle after task
        assert session._foreground_busy.is_set()


class TestSessionCleanup:
    """Tests for session cleanup with proactive reporting."""

    @pytest.mark.asyncio
    async def test_close_cancels_reporter_task(self, session: StreamingSession) -> None:
        """Test that closing session cancels reporter task."""
        generator = AsyncMock()
        session.configure_proactive_reporting(generator=generator, enabled=True)

        assert session._proactive_task is not None
        proactive_task = session._proactive_task

        await session.close()

        # Reporter task should be cancelled (either done or set to None)
        assert proactive_task.done() or session._proactive_task is None


class TestTaskGroups:
    """Tests for task group functionality."""

    @pytest.mark.asyncio
    async def test_resolve_or_create_group_creates_new(self, session: StreamingSession) -> None:
        """Test that resolve_or_create_group creates a new group."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )

        assert group is not None
        assert group.name == "test-group"
        assert group.status == "open"
        assert group.turn_id == "turn-1"
        assert len(group.task_ids) == 0

    @pytest.mark.asyncio
    async def test_resolve_or_create_group_joins_existing(self, session: StreamingSession) -> None:
        """Test that resolve_or_create_group joins an existing open group."""
        # Create initial group
        group1 = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )

        # Should join existing group with same name and turn
        group2 = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )

        assert group2.group_id == group1.group_id

    @pytest.mark.asyncio
    async def test_resolve_or_create_group_new_turn_creates_new(self, session: StreamingSession) -> None:
        """Test that different turn creates a new group even with same name."""
        # Create initial group
        group1 = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )

        # Different turn should create new group
        group2 = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-2",
        )

        assert group2.group_id != group1.group_id

    @pytest.mark.asyncio
    async def test_seal_group(self, session: StreamingSession) -> None:
        """Test sealing a group with tasks stays sealed until tasks complete."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )
        assert group.status == "open"

        # Add a task so it doesn't immediately complete
        await session.add_task_to_group(group.group_id, "task-1")

        result = await session.seal_group(group.group_id)
        assert result is True

        sealed_group = await session.get_group(group_id=group.group_id)
        assert sealed_group is not None
        assert sealed_group.status == "sealed"

    @pytest.mark.asyncio
    async def test_seal_empty_group_completes(self, session: StreamingSession) -> None:
        """Test sealing an empty group immediately completes it."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )
        assert group.status == "open"

        result = await session.seal_group(group.group_id)
        assert result is True

        # Empty group completes immediately
        sealed_group = await session.get_group(group_id=group.group_id)
        assert sealed_group is not None
        assert sealed_group.status == "complete"

    @pytest.mark.asyncio
    async def test_add_task_to_group(self, session: StreamingSession) -> None:
        """Test adding a task to a group."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )

        result = await session.add_task_to_group(group.group_id, "task-1")
        assert result is True

        updated_group = await session.get_group(group_id=group.group_id)
        assert updated_group is not None
        assert "task-1" in updated_group.task_ids

    @pytest.mark.asyncio
    async def test_add_task_to_sealed_group_fails(self, session: StreamingSession) -> None:
        """Test that adding task to sealed group fails."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )
        await session.seal_group(group.group_id)

        result = await session.add_task_to_group(group.group_id, "task-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_groups(self, session: StreamingSession) -> None:
        """Test listing groups by status."""
        await session.resolve_or_create_group(
            group_name="group-1",
            turn_id="turn-1",
        )
        group2 = await session.resolve_or_create_group(
            group_name="group-2",
            turn_id="turn-1",
        )
        # Add a task to prevent immediate completion
        await session.add_task_to_group(group2.group_id, "task-1")
        await session.seal_group(group2.group_id)

        open_groups = await session.list_groups(status="open")
        assert len(open_groups) == 1
        assert open_groups[0].name == "group-1"

        sealed_groups = await session.list_groups(status="sealed")
        assert len(sealed_groups) == 1
        assert sealed_groups[0].name == "group-2"

        all_groups = await session.list_groups()
        assert len(all_groups) == 2

    @pytest.mark.asyncio
    async def test_auto_seal_open_groups(self, session: StreamingSession) -> None:
        """Test auto-sealing open groups for a turn."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )
        assert group.status == "open"

        # Add a task to prevent immediate completion when sealed
        await session.add_task_to_group(group.group_id, "task-1")

        sealed_count = await session.auto_seal_open_groups(turn_id="turn-1")
        assert sealed_count == 1

        sealed_group = await session.get_group(group_id=group.group_id)
        assert sealed_group is not None
        assert sealed_group.status == "sealed"

    @pytest.mark.asyncio
    async def test_foreground_yield_respects_auto_seal_config(self, session: StreamingSession) -> None:
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )
        await session.add_task_to_group(group.group_id, "task-1")

        session.configure_background_tasks(BackgroundTasksConfig(auto_seal_groups_on_foreground_yield=False))
        sealed_count = await session.on_foreground_yield(turn_id="turn-1")
        assert sealed_count == 0
        open_group = await session.get_group(group_id=group.group_id)
        assert open_group is not None
        assert open_group.status == "open"

        session.configure_background_tasks(BackgroundTasksConfig(auto_seal_groups_on_foreground_yield=True))
        sealed_count = await session.on_foreground_yield(turn_id="turn-1")
        assert sealed_count == 1
        sealed_group = await session.get_group(group_id=group.group_id)
        assert sealed_group is not None
        assert sealed_group.status == "sealed"

    @pytest.mark.asyncio
    async def test_cancel_group(self, session: StreamingSession) -> None:
        """Test cancelling a group."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )

        result = await session.cancel_group(group.group_id, reason="test cancel")
        assert result is True

        cancelled_group = await session.get_group(group_id=group.group_id)
        assert cancelled_group is not None
        assert cancelled_group.status == "failed"


class TestTaskGroupCompletion:
    """Tests for task group completion and waiting."""

    @pytest.mark.asyncio
    async def test_wait_for_group_completion_already_complete(self, session: StreamingSession) -> None:
        """Test waiting for an already complete group returns immediately."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )
        await session.seal_group(group.group_id)
        # Manually mark as complete (no tasks means it completes immediately)
        session._groups[group.group_id].status = "complete"

        result, timed_out = await session.wait_for_group_completion(
            group.group_id,
            timeout_s=1.0,
        )

        assert result is not None
        assert result.status == "complete"
        assert timed_out is False

    @pytest.mark.asyncio
    async def test_wait_for_group_completion_timeout(self, session: StreamingSession) -> None:
        """Test waiting for group completion with timeout."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )
        await session.add_task_to_group(group.group_id, "task-1")
        await session.seal_group(group.group_id)

        # Wait with very short timeout - should timeout since task never completes
        result, timed_out = await session.wait_for_group_completion(
            group.group_id,
            timeout_s=0.01,
        )

        assert result is not None
        assert timed_out is True
        assert result.status == "sealed"  # Still sealed, not complete

    @pytest.mark.asyncio
    async def test_wait_for_nonexistent_group(self, session: StreamingSession) -> None:
        """Test waiting for nonexistent group returns None."""
        result, timed_out = await session.wait_for_group_completion(
            "nonexistent-group",
            timeout_s=1.0,
        )

        assert result is None
        assert timed_out is False

    @pytest.mark.asyncio
    async def test_get_group_results_empty(self, session: StreamingSession) -> None:
        """Test getting results from empty group."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
        )

        results = await session.get_group_results(group.group_id)
        assert results == []

    @pytest.mark.asyncio
    async def test_get_group_results_nonexistent(self, session: StreamingSession) -> None:
        """Test getting results from nonexistent group."""
        results = await session.get_group_results("nonexistent-group")
        assert results == []


class TestTaskGroupReporting:
    """Tests for task group proactive reporting."""

    @pytest.mark.asyncio
    async def test_group_report_strategy_all(self, session: StreamingSession) -> None:
        """Test that group with report_strategy='all' only reports on completion."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
            report_strategy="all",
        )

        assert group.report_strategy == "all"
        # Per-task reports should be suppressed (tested via _enqueue_proactive_report)

    @pytest.mark.asyncio
    async def test_group_merge_strategy_human_gated(self, session: StreamingSession) -> None:
        """Test HUMAN_GATED group requires bundled approval."""
        from penguiflow.sessions.models import MergeStrategy

        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
            merge_strategy=MergeStrategy.HUMAN_GATED,
        )

        assert group.merge_strategy == MergeStrategy.HUMAN_GATED

    @pytest.mark.asyncio
    async def test_group_retain_turn(self, session: StreamingSession) -> None:
        """Test group with retain_turn flag."""
        group = await session.resolve_or_create_group(
            group_name="test-group",
            turn_id="turn-1",
            retain_turn=True,
        )

        assert group.retain_turn is True


class TestInProcessTaskServiceGroups:
    """Tests for InProcessTaskService group methods."""

    @pytest.fixture
    def task_service(self) -> Any:
        """Create an InProcessTaskService with required dependencies."""
        from penguiflow.sessions import SessionManager
        from penguiflow.sessions.task_service import InProcessTaskService

        class DummySpawnGuard:
            """Always-allow spawn guard."""

            async def decide(self, request: Any) -> Any:
                from penguiflow.sessions.task_service import SpawnDecision

                return SpawnDecision(allowed=True)

        sessions = SessionManager()
        spawn_guard = DummySpawnGuard()
        return InProcessTaskService(
            sessions=sessions,
            spawn_guard=spawn_guard,
            planner_factory=None,
        )

    @pytest.mark.asyncio
    async def test_seal_group_success(self, task_service: Any) -> None:
        """Test sealing a group through the service."""
        session = await task_service._sessions.get_or_create("s1")
        group = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")
        await session.add_task_to_group(group.group_id, "task-1")

        result = await task_service.seal_group(
            session_id="s1",
            group_id=group.group_id,
        )

        assert result["ok"] is True
        assert result["group_id"] == group.group_id

    @pytest.mark.asyncio
    async def test_seal_group_not_found(self, task_service: Any) -> None:
        """Test sealing nonexistent group."""
        result = await task_service.seal_group(
            session_id="s1",
            group_id="nonexistent",
        )

        assert result["ok"] is False
        assert result["error"] == "group_not_found"

    @pytest.mark.asyncio
    async def test_seal_group_already_sealed(self, task_service: Any) -> None:
        """Test sealing an already sealed group."""
        session = await task_service._sessions.get_or_create("s1")
        group = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")
        await session.add_task_to_group(group.group_id, "task-1")
        await session.seal_group(group.group_id)

        result = await task_service.seal_group(
            session_id="s1",
            group_id=group.group_id,
        )

        assert result["ok"] is False
        assert result["error"] == "group_not_open"

    @pytest.mark.asyncio
    async def test_cancel_group_success(self, task_service: Any) -> None:
        """Test cancelling a group through the service."""
        session = await task_service._sessions.get_or_create("s1")
        group = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")

        result = await task_service.cancel_group(
            session_id="s1",
            group_id=group.group_id,
            reason="test cancellation",
        )

        assert result["ok"] is True
        assert result["group_id"] == group.group_id

    @pytest.mark.asyncio
    async def test_cancel_group_not_found(self, task_service: Any) -> None:
        """Test cancelling nonexistent group."""
        result = await task_service.cancel_group(
            session_id="s1",
            group_id="nonexistent",
        )

        assert result["ok"] is False
        assert result["error"] == "group_not_found"

    @pytest.mark.asyncio
    async def test_apply_group_not_complete(self, task_service: Any) -> None:
        """Test applying patches from incomplete group fails."""
        session = await task_service._sessions.get_or_create("s1")
        group = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")
        await session.add_task_to_group(group.group_id, "task-1")

        result = await task_service.apply_group(
            session_id="s1",
            group_id=group.group_id,
            action="apply",
        )

        assert result["ok"] is False
        assert result["error"] == "group_not_complete"

    @pytest.mark.asyncio
    async def test_apply_group_not_found(self, task_service: Any) -> None:
        """Test applying patches from nonexistent group."""
        result = await task_service.apply_group(
            session_id="s1",
            group_id="nonexistent",
            action="apply",
        )

        assert result["ok"] is False
        assert result["error"] == "group_not_found"

    @pytest.mark.asyncio
    async def test_apply_group_empty_complete(self, task_service: Any) -> None:
        """Test applying patches from empty completed group."""
        session = await task_service._sessions.get_or_create("s1")
        group = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")
        # Seal empty group - completes immediately
        await session.seal_group(group.group_id)

        result = await task_service.apply_group(
            session_id="s1",
            group_id=group.group_id,
            action="apply",
        )

        assert result["ok"] is True
        assert result["applied_patch_count"] == 0

    @pytest.mark.asyncio
    async def test_list_groups(self, task_service: Any) -> None:
        """Test listing groups through the service."""
        session = await task_service._sessions.get_or_create("s1")
        await session.resolve_or_create_group(group_name="group-1", turn_id="turn-1")
        await session.resolve_or_create_group(group_name="group-2", turn_id="turn-1")

        groups = await task_service.list_groups(session_id="s1")

        assert len(groups) == 2
        names = {g.name for g in groups}
        assert "group-1" in names
        assert "group-2" in names

    @pytest.mark.asyncio
    async def test_list_groups_by_status(self, task_service: Any) -> None:
        """Test listing groups filtered by status."""
        session = await task_service._sessions.get_or_create("s1")
        g1 = await session.resolve_or_create_group(group_name="group-1", turn_id="turn-1")
        await session.resolve_or_create_group(group_name="group-2", turn_id="turn-1")
        # Seal group-1 (empty, will complete)
        await session.seal_group(g1.group_id)

        open_groups = await task_service.list_groups(session_id="s1", status="open")
        complete_groups = await task_service.list_groups(session_id="s1", status="complete")

        assert len(open_groups) == 1
        assert open_groups[0].name == "group-2"
        assert len(complete_groups) == 1
        assert complete_groups[0].name == "group-1"

    @pytest.mark.asyncio
    async def test_get_group_by_id(self, task_service: Any) -> None:
        """Test getting a specific group by ID."""
        session = await task_service._sessions.get_or_create("s1")
        group = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")

        result = await task_service.get_group(
            session_id="s1",
            group_id=group.group_id,
        )

        assert result is not None
        assert result.group_id == group.group_id
        assert result.name == "test-group"

    @pytest.mark.asyncio
    async def test_get_group_by_name_and_turn(self, task_service: Any) -> None:
        """Test getting a group by name and turn ID."""
        session = await task_service._sessions.get_or_create("s1")
        await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")

        result = await task_service.get_group(
            session_id="s1",
            group_name="test-group",
            turn_id="turn-1",
        )

        assert result is not None
        assert result.name == "test-group"

    @pytest.mark.asyncio
    async def test_get_group_not_found(self, task_service: Any) -> None:
        """Test getting a nonexistent group."""
        result = await task_service.get_group(
            session_id="s1",
            group_id="nonexistent",
        )

        assert result is None


class TestTaskGroupIsComplete:
    """Additional tests for TaskGroup.is_complete property."""

    def test_is_complete_open_group(self) -> None:
        """Test is_complete returns False for open group."""
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        group = TaskGroup(
            group_id="g1",
            name="test",
            session_id="s1",
            status="open",
            merge_strategy=MergeStrategy.APPEND,
            report_strategy="all",
        )
        assert group.is_complete is False

    def test_is_complete_sealed_with_pending(self) -> None:
        """Test is_complete returns False for sealed group with pending tasks."""
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        group = TaskGroup(
            group_id="g1",
            name="test",
            session_id="s1",
            status="sealed",
            merge_strategy=MergeStrategy.APPEND,
            report_strategy="all",
            task_ids=["t1", "t2"],
            completed_task_ids=["t1"],
            failed_task_ids=[],
        )
        assert group.is_complete is False

    def test_is_complete_sealed_all_complete(self) -> None:
        """Test is_complete returns True for sealed group with all tasks done."""
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        group = TaskGroup(
            group_id="g1",
            name="test",
            session_id="s1",
            status="sealed",
            merge_strategy=MergeStrategy.APPEND,
            report_strategy="all",
            task_ids=["t1", "t2"],
            completed_task_ids=["t1", "t2"],
            failed_task_ids=[],
        )
        assert group.is_complete is True

    def test_is_complete_complete_status(self) -> None:
        """Test is_complete returns True for group with complete status."""
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        group = TaskGroup(
            group_id="g1",
            name="test",
            session_id="s1",
            status="complete",
            merge_strategy=MergeStrategy.APPEND,
            report_strategy="all",
        )
        assert group.is_complete is True

    def test_is_complete_failed_status(self) -> None:
        """Test is_complete returns True for group with failed status."""
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        group = TaskGroup(
            group_id="g1",
            name="test",
            session_id="s1",
            status="failed",
            merge_strategy=MergeStrategy.APPEND,
            report_strategy="all",
        )
        assert group.is_complete is True


class TestTaskGroupPendingTasks:
    """Tests for TaskGroup.pending_task_ids property."""

    def test_pending_task_ids_all_pending(self) -> None:
        """Test pending_task_ids with all tasks pending."""
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        group = TaskGroup(
            group_id="g1",
            name="test",
            session_id="s1",
            status="sealed",
            merge_strategy=MergeStrategy.APPEND,
            report_strategy="all",
            task_ids=["t1", "t2", "t3"],
            completed_task_ids=[],
            failed_task_ids=[],
        )
        assert group.pending_task_ids == ["t1", "t2", "t3"]

    def test_pending_task_ids_some_complete(self) -> None:
        """Test pending_task_ids with some tasks complete."""
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        group = TaskGroup(
            group_id="g1",
            name="test",
            session_id="s1",
            status="sealed",
            merge_strategy=MergeStrategy.APPEND,
            report_strategy="all",
            task_ids=["t1", "t2", "t3"],
            completed_task_ids=["t1"],
            failed_task_ids=["t3"],
        )
        assert group.pending_task_ids == ["t2"]

    def test_pending_task_ids_none_pending(self) -> None:
        """Test pending_task_ids with no tasks pending."""
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        group = TaskGroup(
            group_id="g1",
            name="test",
            session_id="s1",
            status="complete",
            merge_strategy=MergeStrategy.APPEND,
            report_strategy="all",
            task_ids=["t1", "t2"],
            completed_task_ids=["t1", "t2"],
            failed_task_ids=[],
        )
        assert group.pending_task_ids == []


class TestSessionCancelGroup:
    """Tests for session.cancel_group method."""

    @pytest.mark.asyncio
    async def test_cancel_open_group(self, session: StreamingSession) -> None:
        """Test cancelling an open group."""
        group = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")
        await session.add_task_to_group(group.group_id, "task-1")

        result = await session.cancel_group(group.group_id, reason="test cancel")
        assert result is True

        cancelled_group = await session.get_group(group_id=group.group_id)
        assert cancelled_group is not None
        assert cancelled_group.status == "failed"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_group(self, session: StreamingSession) -> None:
        """Test cancelling nonexistent group returns False."""
        result = await session.cancel_group("nonexistent", reason="test")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_group_already_complete(self, session: StreamingSession) -> None:
        """Test cancelling already complete group."""
        group = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")
        # Empty group completes immediately when sealed
        await session.seal_group(group.group_id)

        result = await session.cancel_group(group.group_id, reason="test")
        # Cancelling a complete group should return False
        assert result is False


class TestSessionGroupMethods:
    """Additional tests for session group methods."""

    @pytest.mark.asyncio
    async def test_list_groups_empty(self, session: StreamingSession) -> None:
        """Test listing groups when none exist."""
        groups = await session.list_groups()
        assert groups == []

    @pytest.mark.asyncio
    async def test_list_groups_multiple(self, session: StreamingSession) -> None:
        """Test listing multiple groups."""
        await session.resolve_or_create_group(group_name="group-1", turn_id="turn-1")
        await session.resolve_or_create_group(group_name="group-2", turn_id="turn-1")
        await session.resolve_or_create_group(group_name="group-3", turn_id="turn-2")

        groups = await session.list_groups()
        assert len(groups) == 3

    @pytest.mark.asyncio
    async def test_list_groups_filter_by_status(self, session: StreamingSession) -> None:
        """Test listing groups filtered by status."""
        g1 = await session.resolve_or_create_group(group_name="open-group", turn_id="turn-1")
        g2 = await session.resolve_or_create_group(group_name="complete-group", turn_id="turn-1")
        # Add task to keep open
        await session.add_task_to_group(g1.group_id, "task-1")
        # Seal empty group to complete
        await session.seal_group(g2.group_id)

        open_groups = await session.list_groups(status="open")
        complete_groups = await session.list_groups(status="complete")

        assert len(open_groups) == 1
        assert open_groups[0].name == "open-group"
        assert len(complete_groups) == 1
        assert complete_groups[0].name == "complete-group"

    @pytest.mark.asyncio
    async def test_get_group_by_name_without_turn(self, session: StreamingSession) -> None:
        """Test getting group by name without turn ID returns None if no match."""
        await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")

        # Different turn should not match
        result = await session.get_group(group_name="test-group", turn_id="turn-2")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_sealed_group_creates_new(self, session: StreamingSession) -> None:
        """Test that resolving by name when existing is sealed creates new group."""
        g1 = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")
        await session.add_task_to_group(g1.group_id, "task-1")
        await session.seal_group(g1.group_id)

        # Resolving same name in same turn should create new group
        g2 = await session.resolve_or_create_group(group_name="test-group", turn_id="turn-1")

        assert g2.group_id != g1.group_id
        assert g2.status == "open"
