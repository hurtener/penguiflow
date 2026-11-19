"""ReactPlanner LLM tracing and pricing example."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.llm.pricing import (
    calculate_cost,
    get_pricing,
    register_pricing,
)
from penguiflow.llm.protocol import create_native_adapter
from penguiflow.llm.tracing import LLMCallSpan, resolve_trace_sink_from_env
from penguiflow.node import Node
from penguiflow.planner import PlannerFinish, ReactPlanner, ToolContext
from penguiflow.planner.models import ReasoningJSONLLMClient
from penguiflow.registry import ModelRegistry

MODEL = "databricks-gpt-5-4-mini"


class TicketRequest(BaseModel):
    """Ticket request payload."""

    ticket_id: str


class TicketSummary(BaseModel):
    """Ticket summary payload."""

    ticket_id: str
    status: str
    owner: str


@tool(desc="Lookup a generic support ticket", side_effects="read")
async def lookup_ticket(
    args: TicketRequest, _ctx: ToolContext
) -> TicketSummary:
    """Look up a support ticket."""
    return TicketSummary(
        ticket_id=args.ticket_id, status="ready for review", owner="release"
    )


class TracedScriptedLLM(ReasoningJSONLLMClient):
    """JSONLLMClient that runs the example without live API keys."""

    def __init__(
        self, actions: Sequence[Mapping[str, Any]], *, model: str = MODEL
    ) -> None:
        """Replay ``actions`` in order."""
        self.model = model
        self.calls: list[float] = []
        self._payloads = [json.dumps(action) for action in actions]

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        if not self._payloads:
            msg = "No scripted LLM responses left"
            raise AssertionError(msg)

        payload = self._payloads.pop(0)
        input_tokens = max(
            1, len(json.dumps(list(messages), separators=(",", ":"))) // 4
        )
        output_tokens = max(1, len(payload) // 4)
        cost = calculate_cost(
            self.model, input_tokens=input_tokens, output_tokens=output_tokens
        )
        self.calls.append(cost)

        sink = resolve_trace_sink_from_env()
        if sink is None:
            return payload, cost

        span = LLMCallSpan(
            provider="scripted",
            model=self.model,
            response_format_kind=str(response_format.get("type"))
            if response_format
            else None,
            attempts=1,
        )
        with sink.span(span):
            span.input_tokens = input_tokens
            span.output_tokens = output_tokens
            span.content_chars = len(payload)
            span.cost_usd = cost
            return payload, cost


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _configure_mlflow_tracking() -> None:
    if os.environ.get("PENGUIFLOW_LLM_TRACING", "").lower() != "mlflow":
        return
    try:
        import mlflow
    except ImportError:
        return
    tracking_dir = Path(__file__).parent / "mlruns"
    tracking_dir.mkdir(exist_ok=True)
    mlflow.set_tracking_uri(f"file:{tracking_dir.resolve()}")


def _catalog() -> list[Any]:
    registry = ModelRegistry()
    registry.register("lookup_ticket", TicketRequest, TicketSummary)
    return build_catalog([Node(lookup_ticket, name="lookup_ticket")], registry)


def _scripted_client() -> TracedScriptedLLM:
    return TracedScriptedLLM(
        [
            {
                "thought": "Fetch the ticket status before answering.",
                "next_node": "lookup_ticket",
                "args": {"ticket_id": "PF-311"},
            },
            {
                "thought": "The ticket has enough information.",
                "next_node": None,
                "args": {
                    "raw_answer": "PF-311 is ready for review "
                    "and owned by the "
                    "release lane."
                },
            },
        ]
    )


def _live_client() -> ReasoningJSONLLMClient:
    _load_dotenv(Path(__file__).parents[2] / ".env")
    return create_native_adapter(MODEL, max_retries=1, timeout_s=90.0)


async def run_demo(*, live: bool = False) -> dict[str, Any]:
    """Run the demo end to end."""
    _configure_mlflow_tracking()

    client = _live_client() if live else _scripted_client()
    planner = ReactPlanner(llm_client=client, catalog=_catalog(), max_iters=3)
    result = await planner.run("Summarize ticket PF-311 for release review.")

    input_price, output_price = get_pricing(MODEL)
    private_rate_model = "private-databricks-route"
    register_pricing(private_rate_model, 0.00001, 0.00002)
    private_rate_cost = calculate_cost(
        private_rate_model, input_tokens=1000, output_tokens=1000
    )

    llm_cost = sum(getattr(client, "calls", []))
    if live:
        assert isinstance(result, PlannerFinish)
        cost_metadata = result.metadata.get("cost", {})
        if isinstance(cost_metadata, Mapping):
            llm_cost = float(cost_metadata.get("total_cost_usd", 0.0) or 0.0)

    if isinstance(result.payload, Mapping):
        answer = str(result.payload.get("raw_answer", result.payload))
    else:
        answer = str(result.payload.raw_answer)

    return {
        "answer": answer,
        "model": MODEL,
        "input_price_per_1k": input_price,
        "output_price_per_1k": output_price,
        "llm_cost_usd": llm_cost,
        "private_rate_cost_usd": private_rate_cost,
    }


async def main() -> None:
    """Run the example."""
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s %(message)s"
    )
    live = os.environ.get("PENGUIFLOW_PHASE4_LIVE", "").lower() in {
        "1",
        "true",
        "yes",
    }
    summary = await run_demo(live=live)
    sys.stdout.write(f"{json.dumps(summary, indent=2, sort_keys=True)}\n")


if __name__ == "__main__":
    asyncio.run(main())
