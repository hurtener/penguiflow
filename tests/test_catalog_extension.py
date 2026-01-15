from __future__ import annotations

from penguiflow.catalog import build_catalog
from penguiflow.planner import ReactPlanner
from penguiflow.planner.catalog_extension import extend_tool_catalog
from penguiflow.registry import ModelRegistry
from penguiflow.sessions.task_tools import build_task_tool_specs


class MockJSONLLMClient:
    async def send_messages(self, messages, *, response_format=None, stream=False, on_stream_chunk=None):  # type: ignore[no-untyped-def]
        _ = messages, response_format, stream, on_stream_chunk
        return '{"thought":"ok","next_node":null,"args":{"raw_answer":"done"}}'


def test_extend_tool_catalog_adds_specs() -> None:
    planner = ReactPlanner(llm_client=MockJSONLLMClient(), catalog=build_catalog([], ModelRegistry()), max_iters=1)
    added = extend_tool_catalog(planner, build_task_tool_specs())
    assert added > 0
    assert any(spec.name == "tasks.spawn" for spec in planner._specs)
