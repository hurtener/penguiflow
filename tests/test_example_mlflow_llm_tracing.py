"""Integration test for the MLflow LLM tracing example."""

from __future__ import annotations

import pytest

from examples.mlflow_llm_tracing.flow import run_demo


@pytest.mark.asyncio
async def test_mlflow_llm_tracing_example_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MLflow LLM tracing example runs."""
    monkeypatch.setenv("PENGUIFLOW_LLM_TRACING", "log")

    summary = await run_demo(live=False)

    assert (
        summary["answer"]
        == "PF-311 is ready for review and owned by the release lane."
    )
    assert summary["model"] == "databricks-gpt-5-4-mini"
    assert summary["input_price_per_1k"] == pytest.approx(0.00075)
    assert summary["output_price_per_1k"] == pytest.approx(0.0045)
    assert summary["llm_cost_usd"] > 0
    assert summary["private_rate_cost_usd"] == pytest.approx(0.00003)
