"""Typed ``ReactPlanner`` options for test helpers that build planners.

Helpers such as ``make_planner(client, **options)`` forward keyword options
to ``ReactPlanner``. ``PlannerOptions`` lists the options tests pass, with
the planner's own types, so the forwarding stays type-checked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import BaseModel

    from penguiflow.planner import PlannerEventCallback
    from penguiflow.planner.llm_context_hooks import LLMContextHook
    from penguiflow.planner.memory import (
        ShortTermMemory,
        ShortTermMemoryConfig,
    )
    from penguiflow.state import StateStore


class PlannerOptions(TypedDict, total=False):
    """``ReactPlanner`` keyword options used by the tests."""

    event_callback: PlannerEventCallback | None
    final_response_model: type[BaseModel] | None
    final_response_retries: int
    llm_context_hooks: Sequence[LLMContextHook] | None
    max_iters: int
    multi_action_max_tools: int
    multi_action_read_only_only: bool
    multi_action_sequential: bool
    planning_hints: Mapping[str, Any] | None
    repair_attempts: int
    short_term_memory: ShortTermMemory | ShortTermMemoryConfig | None
    state_store: StateStore | None
    stream_final_response: bool
    token_budget: int | None
