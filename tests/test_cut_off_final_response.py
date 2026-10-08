"""A final answer cut off at the output-token limit is recovered once, not re-asked in full."""

from __future__ import annotations

from typing import Any

import pytest

from penguiflow.catalog import build_catalog
from penguiflow.planner import ReactPlanner
from penguiflow.planner.react_step import CUT_OFF_ANSWER_NOTE
from penguiflow.planner.trajectory import Trajectory
from penguiflow.registry import ModelRegistry


class RawClient:
    """Returns raw text, so a test can hand the planner JSON that never closes."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def complete(self, *, messages: Any, response_format: Any = None, stream: bool = False, on_stream_chunk: Any = None) -> tuple[str, float]:
        self.calls += 1
        return self._responses.pop(0), 0.0


def _planner(client: RawClient) -> ReactPlanner:
    return ReactPlanner(llm_client=client, catalog=build_catalog([], ModelRegistry()))


CUT_OFF = '{"next_node": "final_response", "args": {"answer": "' + "Campaign 1 delivered 1,000 impressions. " * 120 + "Campaign 2 deliv"


@pytest.mark.asyncio
async def test_a_cut_off_final_answer_costs_one_call_not_three() -> None:
    """Re-asking made the model rewrite and re-truncate the answer three times: 268s of a 281s run."""

    client = RawClient([CUT_OFF, CUT_OFF, CUT_OFF])
    action = await _planner(client).step(Trajectory(query="q"))
    assert client.calls == 1
    assert action.next_node == "final_response"


@pytest.mark.asyncio
async def test_the_recovered_answer_says_it_was_cut_off() -> None:
    client = RawClient([CUT_OFF])
    action = await _planner(client).step(Trajectory(query="q"))
    assert action.args["answer"].startswith("Campaign 1 delivered")
    assert action.args["answer"].endswith(CUT_OFF_ANSWER_NOTE)


@pytest.mark.asyncio
async def test_short_malformed_json_is_still_repaired_by_asking_again() -> None:
    """Only a long final answer is treated as cut off; an ordinary malformed reply keeps its retries."""

    client = RawClient(['{"next_node": "final_response", "args": {"answer": "ok', '{"next_node": "final_response", "args": {"answer": "ok"}}'])
    action = await _planner(client).step(Trajectory(query="q"))
    assert client.calls == 2
    assert action.args["answer"] == "ok"
