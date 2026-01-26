from __future__ import annotations

import pytest

from penguiflow.sessions import ContextPatch, MergeStrategy, StreamingSession


@pytest.mark.asyncio
async def test_human_gated_merge_creates_pending_patch() -> None:
    session = StreamingSession("session-merge")
    session.update_context(llm_context={"state": "v1"})
    patch = ContextPatch(
        task_id="task-1",
        digest=["summary"],
        source_context_version=0,
        source_context_hash="stale",
    )
    patch_id = await session.apply_context_patch(patch=patch, strategy=MergeStrategy.HUMAN_GATED)
    assert patch_id is not None
    pending = session.pending_patches[patch_id]
    assert pending.patch.context_diverged is True


@pytest.mark.asyncio
async def test_apply_pending_patch_updates_context() -> None:
    session = StreamingSession("session-merge-apply")
    patch = ContextPatch(
        task_id="task-2",
        digest=["summary"],
    )
    patch_id = await session.apply_context_patch(patch=patch, strategy=MergeStrategy.HUMAN_GATED)
    assert patch_id is not None
    applied = await session.apply_pending_patch(patch_id=patch_id)
    assert applied is True
    background = session.get_background_results()
    assert "task-2" in background
    llm_context, _tool = session.get_context()
    assert "background_results" not in llm_context


@pytest.mark.asyncio
async def test_mark_background_consumed_removes_entries() -> None:
    session = StreamingSession("session-merge-consume")
    append_patch = ContextPatch(task_id="task-append", digest=["summary"])
    replace_patch = ContextPatch(task_id="task-replace", digest=["latest"])
    await session.apply_context_patch(patch=append_patch, strategy=MergeStrategy.APPEND)
    await session.apply_context_patch(patch=replace_patch, strategy=MergeStrategy.REPLACE)

    background = session.get_background_results()
    assert set(background.keys()) == {"task-append", "task-replace"}
    llm_context, _tool = session.get_context()
    assert "background_results" not in llm_context
    assert "background_result" not in llm_context

    removed = await session.mark_background_consumed(task_ids=["task-append", "task-replace"])
    assert removed == 2
    background = session.get_background_results()
    assert not background
    llm_context, _tool = session.get_context()
    assert "background_results" not in llm_context
    assert "background_result" not in llm_context
    removed_again = await session.mark_background_consumed(task_ids=["task-append"])
    assert removed_again == 0
