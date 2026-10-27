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


_NOTE_LINE = "Campaign 1 ran 2026-09-28 to 2026-10-12.\\n"
STRUCTURED_CUT_OFF = (
    '```json\n{"next_node": "final_response", "args": {"answer": "See the list.", "structured": '
    '{"coverage": "Every campaign in the table", "tables": ["totals"], '
    '"bullets": ["First finding.", "Second finding."], "note": "' + _NOTE_LINE * 120 + "Campaign 2 ra"
)


@pytest.mark.asyncio
async def test_a_cut_off_structured_answer_keeps_the_readable_part_of_its_note() -> None:
    """A long list goes in the structured note; cut off mid-note, the placeholder was all the reader got."""

    action = await _planner(RawClient([STRUCTURED_CUT_OFF])).step(Trajectory(query="q"))

    note = action.args["structured_rejected"]["note"]
    assert note.startswith("Campaign 1 ran 2026-09-28")
    assert note.count("Campaign 1 ran") == 120


@pytest.mark.asyncio
async def test_the_kept_note_drops_the_half_written_last_line_and_says_it_was_cut_off() -> None:
    action = await _planner(RawClient([STRUCTURED_CUT_OFF])).step(Trajectory(query="q"))

    note = action.args["structured_rejected"]["note"]
    assert "Campaign 2 ra" not in note
    assert note.endswith(CUT_OFF_ANSWER_NOTE)


@pytest.mark.asyncio
async def test_the_kept_note_is_real_text_not_json_escapes() -> None:
    action = await _planner(RawClient([STRUCTURED_CUT_OFF])).step(Trajectory(query="q"))

    note = action.args["structured_rejected"]["note"]
    assert "\n" in note
    assert "\\n" not in note


@pytest.mark.asyncio
async def test_the_fields_before_a_cut_off_note_are_kept_whole() -> None:
    action = await _planner(RawClient([STRUCTURED_CUT_OFF])).step(Trajectory(query="q"))

    structured = action.args["structured_rejected"]
    assert structured["coverage"] == "Every campaign in the table"
    assert structured["tables"] == ["totals"]
    assert structured["bullets"] == ["First finding.", "Second finding."]


@pytest.mark.asyncio
async def test_a_cut_off_structured_answer_still_costs_one_call() -> None:
    client = RawClient([STRUCTURED_CUT_OFF, STRUCTURED_CUT_OFF, STRUCTURED_CUT_OFF])

    await _planner(client).step(Trajectory(query="q"))

    assert client.calls == 1


@pytest.mark.asyncio
async def test_a_cut_off_structured_answer_still_carries_its_short_answer() -> None:
    action = await _planner(RawClient([STRUCTURED_CUT_OFF])).step(Trajectory(query="q"))

    assert action.args["answer"].startswith("See the list.")


@pytest.mark.asyncio
async def test_a_cut_off_answer_with_no_structured_part_gets_no_structured_object() -> None:
    action = await _planner(RawClient([CUT_OFF])).step(Trajectory(query="q"))

    assert "structured_rejected" not in action.args


@pytest.mark.asyncio
async def test_nothing_is_invented_when_the_cut_comes_before_the_note() -> None:
    before_the_note = (
        '{"next_node": "final_response", "args": {"answer": "' + "x" * 2500
        + '", "structured": {"coverage": "Every camp'
    )

    action = await _planner(RawClient([before_the_note])).step(Trajectory(query="q"))

    assert "structured_rejected" not in action.args


@pytest.mark.asyncio
async def test_short_malformed_json_is_still_repaired_by_asking_again() -> None:
    """Only a long final answer is treated as cut off; an ordinary malformed reply keeps its retries."""

    client = RawClient(['{"next_node": "final_response", "args": {"answer": "ok', '{"next_node": "final_response", "args": {"answer": "ok"}}'])
    action = await _planner(client).step(Trajectory(query="q"))
    assert client.calls == 2
    assert action.args["answer"] == "ok"
