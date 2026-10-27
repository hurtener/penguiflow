from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from penguiflow.evals.helpers import (
    _JudgeResult,
    extract_node_sequence,
    extract_step_args,
    extract_step_subset,
    extract_terminal_node,
    llm_judge,
    sequence_match,
    step_args_match,
    trajectory_subset_match,
)


def test_extract_node_sequence_reads_planner_next_nodes() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query"}},
            {"action": {"next_node": "answer_general"}},
        ]
    }

    assert extract_node_sequence(pred_trace) == ["triage_query", "answer_general"]


def test_extract_node_sequence_returns_empty_for_missing_steps() -> None:
    assert extract_node_sequence({"metadata": {}}) == []


def test_extract_node_sequence_returns_empty_for_non_mapping_trace() -> None:
    assert extract_node_sequence("not-a-trace") == []


def test_extract_node_sequence_skips_malformed_steps() -> None:
    pred_trace = {
        "steps": [
            None,
            {"bad": "step"},
            {"action": None},
            {"action": {"next_node": "triage_query"}},
            {"action": {"next_node": 123}},
            {"action": {"next_node": ""}},
        ]
    }

    assert extract_node_sequence(pred_trace) == ["triage_query"]


def test_extract_node_sequence_ignores_terminal_none_next_node() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query"}},
            {"action": {"next_node": None}},
        ]
    }

    assert extract_node_sequence(pred_trace) == ["triage_query"]


def test_extract_terminal_node_returns_last_valid_node() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query"}},
            {"action": {"next_node": "answer_general"}},
            {"action": {"next_node": None}},
        ]
    }

    assert extract_terminal_node(pred_trace) == "answer_general"


def test_extract_terminal_node_returns_none_for_missing_or_malformed_trace() -> None:
    assert extract_terminal_node({"metadata": {}}) is None
    assert extract_terminal_node("not-a-trace") is None


def test_extract_terminal_node_skips_malformed_steps() -> None:
    pred_trace = {
        "steps": [
            None,
            {"bad": "step"},
            {"action": None},
            {"action": {"next_node": "triage_query"}},
            {"action": {"next_node": ""}},
        ]
    }

    assert extract_terminal_node(pred_trace) == "triage_query"


def test_extract_step_args_returns_args_in_observed_order() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query", "args": {"query": "hello"}}},
            {"action": {"next_node": "answer_general", "args": {"tone": "brief"}}},
        ]
    }

    assert extract_step_args(pred_trace) == [{"query": "hello"}, {"tone": "brief"}]


def test_extract_step_args_filters_by_node_name() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query", "args": {"query": "hello"}}},
            {"action": {"next_node": "triage_query", "args": {"query": "world"}}},
            {"action": {"next_node": "answer_general", "args": {"tone": "brief"}}},
        ]
    }

    assert extract_step_args(pred_trace, node_name="triage_query") == [
        {"query": "hello"},
        {"query": "world"},
    ]


def test_extract_step_args_returns_empty_for_missing_or_malformed_trace() -> None:
    assert extract_step_args({"metadata": {}}) == []
    assert extract_step_args("not-a-trace") == []


def test_extract_step_args_skips_steps_with_missing_or_non_mapping_args() -> None:
    pred_trace = {
        "steps": [
            None,
            {"bad": "step"},
            {"action": None},
            {"action": {"next_node": "triage_query"}},
            {"action": {"next_node": "triage_query", "args": None}},
            {"action": {"next_node": "triage_query", "args": "bad"}},
            {"action": {"next_node": "triage_query", "args": {"query": "ok"}}},
        ]
    }

    assert extract_step_args(pred_trace) == [{"query": "ok"}]


def test_extract_step_subset_returns_full_args_when_fields_omitted() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query", "args": {"query": "hello", "priority": "high"}}},
            {"action": {"next_node": "answer_general", "args": {"tone": "brief"}}},
        ]
    }

    assert extract_step_subset(pred_trace) == [
        {"query": "hello", "priority": "high"},
        {"tone": "brief"},
    ]


def test_extract_step_subset_selects_requested_fields_only() -> None:
    pred_trace = {
        "steps": [
            {
                "action": {
                    "next_node": "triage_query",
                    "args": {"query": "hello", "priority": "high", "ignored": True},
                }
            },
            {"action": {"next_node": "answer_general", "args": {"tone": "brief", "ignored": True}}},
        ]
    }

    assert extract_step_subset(pred_trace, fields=["query", "tone"]) == [
        {"query": "hello"},
        {"tone": "brief"},
    ]


def test_extract_step_subset_filters_by_node_name() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query", "args": {"query": "hello", "priority": "high"}}},
            {"action": {"next_node": "triage_query", "args": {"query": "world", "priority": "low"}}},
            {"action": {"next_node": "answer_general", "args": {"tone": "brief"}}},
        ]
    }

    assert extract_step_subset(pred_trace, node_name="triage_query", fields=["query"]) == [
        {"query": "hello"},
        {"query": "world"},
    ]


def test_extract_step_subset_skips_missing_requested_fields_and_malformed_steps() -> None:
    pred_trace = {
        "steps": [
            None,
            {"bad": "step"},
            {"action": None},
            {"action": {"next_node": "triage_query"}},
            {"action": {"next_node": "triage_query", "args": None}},
            {"action": {"next_node": "triage_query", "args": "bad"}},
            {"action": {"next_node": "triage_query", "args": {"query": "ok", "priority": "high"}}},
            {"action": {"next_node": "triage_query", "args": {"priority": "low"}}},
        ]
    }

    assert extract_step_subset(pred_trace, fields=["query"]) == [{"query": "ok"}, {}]


def test_sequence_match_strict_requires_exact_order() -> None:
    assert sequence_match(["triage", "answer"], ["triage", "answer"], mode="strict") is True
    assert sequence_match(["triage", "answer"], ["answer", "triage"], mode="strict") is False


def test_sequence_match_unordered_ignores_order_but_respects_multiplicity() -> None:
    assert sequence_match(["triage", "answer"], ["answer", "triage"], mode="unordered") is True
    assert sequence_match(["triage", "triage"], ["triage"], mode="unordered") is False


def test_sequence_match_subset_allows_extra_actual_items_in_order() -> None:
    assert sequence_match(["triage", "search", "answer"], ["triage", "answer"], mode="subset") is True
    assert sequence_match(["triage", "search", "answer"], ["answer", "triage"], mode="subset") is False


def test_sequence_match_superset_requires_actual_to_fit_inside_expected_order() -> None:
    assert sequence_match(["triage", "answer"], ["triage", "search", "answer"], mode="superset") is True
    assert sequence_match(["triage", "search", "answer"], ["triage", "answer"], mode="superset") is False


def test_sequence_match_subset_handles_repeated_items_deterministically() -> None:
    assert sequence_match(["triage", "search", "search", "answer"], ["search", "answer"], mode="subset") is True
    assert sequence_match(["triage", "search", "answer"], ["search", "search"], mode="subset") is False


def test_sequence_match_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        sequence_match(["triage"], ["triage"], mode="bad")


def test_step_args_match_subset_requires_expected_pairs_in_actual() -> None:
    assert step_args_match(
        {"query": "hello", "priority": "high"},
        {"query": "hello"},
        mode="subset",
    ) is True
    assert step_args_match(
        {"query": "hello"},
        {"query": "hello", "priority": "high"},
        mode="subset",
    ) is False


def test_step_args_match_superset_requires_actual_pairs_in_expected() -> None:
    assert step_args_match(
        {"query": "hello"},
        {"query": "hello", "priority": "high"},
        mode="superset",
    ) is True
    assert step_args_match(
        {"query": "hello", "priority": "high"},
        {"query": "hello"},
        mode="superset",
    ) is False


def test_step_args_match_fields_narrows_comparison() -> None:
    assert step_args_match(
        {"query": "hello", "priority": "high"},
        {"query": "hello", "priority": "low"},
        mode="subset",
        fields=["query"],
    ) is True
    assert step_args_match(
        {"query": "hello", "priority": "high"},
        {"query": "bye", "priority": "high"},
        mode="subset",
        fields=["query"],
    ) is False


def test_step_args_match_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        step_args_match({"query": "hello"}, {"query": "hello"}, mode="bad")


def test_trajectory_subset_match_allows_extra_observed_route_items() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query", "args": {"query": "hello"}}},
            {"action": {"next_node": "search_documents", "args": {"query": "hello"}}},
            {"action": {"next_node": "answer_general", "args": {"tone": "brief"}}},
        ]
    }

    assert trajectory_subset_match(
        pred_trace,
        {"node_sequence": ["triage_query", "answer_general"]},
        mode="subset",
    ) is True


def test_trajectory_subset_match_superset_fails_when_observed_route_has_extra_items() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query", "args": {"query": "hello"}}},
            {"action": {"next_node": "search_documents", "args": {"query": "hello"}}},
            {"action": {"next_node": "answer_general", "args": {"tone": "brief"}}},
        ]
    }

    assert trajectory_subset_match(
        pred_trace,
        {"node_sequence": ["triage_query", "answer_general"]},
        mode="superset",
    ) is False


def test_trajectory_subset_match_requires_each_expected_arg_spec_to_match_some_observed_call() -> None:
    pred_trace = {
        "steps": [
            {"action": {"next_node": "triage_query", "args": {"query": "hello"}}},
            {"action": {"next_node": "triage_query", "args": {"query": "world"}}},
            {"action": {"next_node": "answer_general", "args": {"tone": "brief"}}},
        ]
    }

    assert trajectory_subset_match(
        pred_trace,
        {
            "node_name": "triage_query",
            "step_args": [{"query": "hello"}, {"query": "world"}],
        },
        mode="subset",
    ) is True
    assert trajectory_subset_match(
        pred_trace,
        {
            "node_name": "triage_query",
            "step_args": [{"query": "hello"}, {"query": "missing"}],
        },
        mode="subset",
    ) is False


def test_trajectory_subset_match_returns_false_for_malformed_trace_when_expectations_exist() -> None:
    assert trajectory_subset_match(
        "not-a-trace",
        {"node_sequence": ["triage_query"]},
        mode="subset",
    ) is False


def test_trajectory_subset_match_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        trajectory_subset_match({"steps": []}, {"node_sequence": []}, mode="bad")


@pytest.mark.asyncio
async def test_llm_judge_uses_prebuilt_client() -> None:
    calls: dict[str, object] = {}

    class _Client:
        async def generate(self, messages, response_model, **kwargs):
            calls["messages"] = messages
            calls["response_model"] = response_model
            calls["kwargs"] = kwargs
            # The real client validates against the response_model it is given,
            # so the stub returns that model rather than a duck-typed stand-in.
            return SimpleNamespace(data=_JudgeResult(score=0.75, feedback="looks good"))

    result = await llm_judge(
        prompt="Judge the answer",
        inputs={"question": "Hi"},
        outputs={"answer": "Hello"},
        client=_Client(),
        temperature=0.2,
        use_reasoning=False,
    )

    assert result == {"score": 0.75, "feedback": "looks good"}
    messages = calls["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert "Judge the answer" in messages[0].text
    assert "question" in messages[0].text
    assert calls["kwargs"] == {"temperature": 0.2}


@pytest.mark.asyncio
async def test_llm_judge_builds_client_from_model(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _Client:
        def __init__(self, model: str) -> None:
            seen["model"] = model

        async def generate(self, messages, response_model, **kwargs):
            seen["messages"] = messages
            seen["response_model"] = response_model
            seen["kwargs"] = kwargs
            return SimpleNamespace(data=_JudgeResult(score=1.0, feedback=None))

    monkeypatch.setattr("penguiflow.evals.helpers.LLMClient", _Client)

    result = await llm_judge(
        prompt="Judge the answer",
        outputs={"answer": "Hello"},
        model="openai/gpt-4o-mini",
    )

    assert seen["model"] == "openai/gpt-4o-mini"
    assert result == {"score": 1.0, "feedback": None}


@pytest.mark.asyncio
async def test_llm_judge_requires_exactly_one_of_client_or_model() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        await llm_judge(prompt="Judge", outputs={"answer": "ok"})

    with pytest.raises(ValueError, match="exactly one"):
        await llm_judge(prompt="Judge", outputs={"answer": "ok"}, client=object(), model="gpt-4o")


@pytest.mark.asyncio
async def test_llm_judge_rejects_unexpected_response_shape() -> None:
    """A response that is not the judge schema must fail with a clear message.

    The score and feedback come straight from model output, so this is a trust
    boundary; reading attributes off whatever arrived would surface as an
    AttributeError deep in a metric run.
    """

    class _Other(BaseModel):
        verdict: str

    class _Client:
        async def generate(self, messages, response_model, **kwargs):
            del messages, response_model, kwargs
            return SimpleNamespace(data=_Other(verdict="great"))

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        await llm_judge(prompt="Judge", outputs={"answer": "ok"}, client=_Client())


@pytest.mark.asyncio
async def test_llm_judge_surfaces_failures_clearly() -> None:
    class _Client:
        async def generate(self, messages, response_model, **kwargs):
            del messages, response_model, kwargs
            raise RuntimeError("provider blew up")

    with pytest.raises(RuntimeError, match="llm_judge failed"):
        await llm_judge(
            prompt="Judge",
            outputs={"answer": "ok"},
            client=_Client(),
        )
