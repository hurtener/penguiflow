"""Opt-in hooks for injecting per-run LLM context.

These hooks run once at the start of a planner run (not on pause/resume), and
may add ephemeral context such as externally retrieved user information.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .memory import MemoryKey


@dataclass(frozen=True, slots=True)
class LLMContextHookInput:
    """Inputs to LLM context hooks.

    `llm_context` and `tool_context` are the *normalized* values that will be used
    for the run. Hooks should treat them as read-only.
    """

    query: str
    llm_context: Mapping[str, Any]
    tool_context: Mapping[str, Any]
    memory_key: MemoryKey | None


class LLMContextHook(Protocol):
    """Hook that can patch llm_context before the first LLM call.

    Hooks are opt-in and best-effort; failures should not block a run.

    Optional attributes (read via getattr):
    - `name: str`: Used for events/logging. Defaults to class name.
    - `overwrite: bool`: When True, the hook may overwrite existing keys.
      Default: False.
    """

    async def before_run(self, inp: LLMContextHookInput) -> Mapping[str, Any] | None: ...

