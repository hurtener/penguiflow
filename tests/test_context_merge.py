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
    llm_context, _tool = session.get_context()
    assert "background_results" in llm_context
