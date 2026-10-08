"""Letting an action point at a previous step's data instead of re-typing it."""

from __future__ import annotations

from types import SimpleNamespace

import json

import pytest

from penguiflow.planner.step_refs import (
    STEP_REF_SUFFIX,
    StepRefError,
    expand_step_refs,
    step_ref_fields,
)

ROWS = [{"acid": "114795", "impressions": 468_471}, {"acid": "109249", "impressions": 592_877_053}]


def _step(observation, tool="aggregate_report"):
    return SimpleNamespace(observation=observation, action=SimpleNamespace(next_node=tool))


def test_a_reference_is_replaced_by_the_data_from_that_step() -> None:
    steps = [_step({"row_count": 2, "rows": ROWS})]

    args, filled = expand_step_refs({"columns": ["acid"], "rows_from_step": 1}, steps)

    assert args == {"columns": ["acid"], "rows": ROWS}
    assert set(filled) == {"rows"}
    assert filled["rows"] == len(json.dumps(ROWS))


def test_arguments_with_no_reference_come_back_unchanged() -> None:
    """A no-op for every tool and every action that does not use it."""

    original = {"columns": ["acid"], "rows": ROWS}

    args, filled = expand_step_refs(original, [_step({"rows": []})])

    assert (args, filled) == (original, {})


def test_several_fields_can_be_referenced_in_one_action() -> None:
    steps = [_step({"rows": ROWS, "overall": {"spend": 10_222.57}})]

    args, filled = expand_step_refs({"rows_from_step": 1, "overall_from_step": 1}, steps)

    assert args["rows"] == ROWS
    assert args["overall"] == {"spend": 10_222.57}
    assert sorted(filled) == ["overall", "rows"]
    assert all(size > 0 for size in filled.values())


def test_the_reference_key_never_survives_into_the_arguments() -> None:
    """The tool's own model forbids unknown keys, so a leftover reference would fail validation —
    and, worse, would ship to the frontend inside the component props."""

    args, _ = expand_step_refs({"rows_from_step": 1}, [_step({"rows": ROWS})])

    assert not any(key.endswith(STEP_REF_SUFFIX) for key in args)


def test_giving_both_the_field_and_a_reference_to_it_is_an_error() -> None:
    with pytest.raises(StepRefError, match="one or the other"):
        expand_step_refs({"rows": ROWS, "rows_from_step": 1}, [_step({"rows": ROWS})])


def test_a_reference_past_the_last_step_says_how_many_there_are() -> None:
    """The model cannot see this code, so the message has to be enough to correct itself from."""

    with pytest.raises(StepRefError, match="are 2 earlier step"):
        expand_step_refs({"rows_from_step": 7}, [_step({"rows": ROWS}), _step({"rows": ROWS})])


def test_a_reference_to_step_zero_is_an_error() -> None:
    """Steps are numbered from 1, the way the planner's own prompt numbers them."""

    with pytest.raises(StepRefError, match="counting from 1"):
        expand_step_refs({"rows_from_step": 0}, [_step({"rows": ROWS})])


def test_a_reference_that_is_not_a_whole_number_is_an_error() -> None:
    with pytest.raises(StepRefError, match="whole number"):
        expand_step_refs({"rows_from_step": "the first one"}, [_step({"rows": ROWS})])


def test_a_boolean_is_not_a_step_number() -> None:
    """True is an int in Python, and silently resolving it to step 1 would be absurd."""

    with pytest.raises(StepRefError, match="whole number"):
        expand_step_refs({"rows_from_step": True}, [_step({"rows": ROWS})])


def test_a_step_with_no_structured_result_names_the_tool_that_ran() -> None:
    with pytest.raises(StepRefError, match="generate_client_email"):
        expand_step_refs({"rows_from_step": 1}, [_step("just text", tool="generate_client_email")])


def test_a_missing_field_lists_what_the_step_did_return() -> None:
    """So the model can pick the right field instead of guessing again."""

    with pytest.raises(StepRefError, match="row_count"):
        expand_step_refs({"rows_from_step": 1}, [_step({"row_count": 0, "warnings": []})])


def test_a_dangling_reference_raises_rather_than_rendering_nothing() -> None:
    """The one behaviour this module exists to guarantee: a reference to data that is not there is a
    loud failure, not an empty table."""

    with pytest.raises(StepRefError):
        expand_step_refs({"rows_from_step": 1}, [])


def test_the_tool_name_appears_in_every_message_when_given() -> None:
    with pytest.raises(StepRefError, match="render_table"):
        expand_step_refs({"rows_from_step": 9}, [_step({"rows": ROWS})], tool="render_table")


def test_the_characters_a_reference_stood_in_for_are_reported() -> None:
    """The only measurement available, and the reason this first attempt was inconclusive. By design
    the expanded arguments are indistinguishable from typed ones by the time anything records them,
    so this count is how a run that used a reference is told apart from one that did not."""

    _, filled = expand_step_refs({"rows_from_step": 1}, [_step({"rows": ROWS})])

    assert filled["rows"] == len(json.dumps(ROWS))


def test_the_fields_supplied_by_reference_can_be_listed_without_expanding() -> None:
    assert step_ref_fields({"rows_from_step": 1, "columns": []}) == ("rows",)
    assert step_ref_fields({"_from_step": 1}) == ()
