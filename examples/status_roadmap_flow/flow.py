"""Roadmap-driven flow emitting websocket-friendly status updates."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel

from penguiflow import (
    Headers,
    Message,
    Node,
    NodePolicy,
    PenguiFlow,
    create,
    flow_to_mermaid,
    map_concurrent,
    predicate_router,
)
from penguiflow.types import StreamChunk


class FlowResponse(BaseModel):
    """Pydantic response contract returned by each subflow."""

    raw_output: str
    artifacts: dict[str, Any] | None = None
    session_info: str | None = None


class RoadmapStep(BaseModel):
    id: int
    name: str
    description: str


class StatusUpdate(BaseModel):
    status: Literal["thinking", "ok"]
    message: str | None = None
    roadmap_step_list: list[RoadmapStep] | None = None
    roadmap_step_id: int | None = None
    roadmap_step_status: Literal["running", "ok"] | None = None


class UserQuery(BaseModel):
    text: str
    session_id: str


class CodeAnalysisRequest(BaseModel):
    kind: Literal["code"]
    query: str
    files: list[str]
    session_id: str


class DataSummaryRequest(BaseModel):
    kind: Literal["data"]
    query: str
    tables: list[str]
    session_id: str


FINAL_STEP = RoadmapStep(
    id=4,
    name="Synthesize final reply",
    description="Combine subflow output and compose the user response",
)

CODE_STEPS: list[RoadmapStep] = [
    RoadmapStep(
        id=1, name="Parse files", description="Load and tokenize the candidate modules"
    ),
    RoadmapStep(
        id=2,
        name="Inspect modules",
        description="Review each module in parallel to collect findings",
    ),
    RoadmapStep(
        id=3,
        name="Draft code report",
        description="Summarize findings and prepare a structured FlowResponse",
    ),
    FINAL_STEP,
]

DATA_STEPS: list[RoadmapStep] = [
    RoadmapStep(id=1, name="Collect metrics", description="Query analytics tables"),
    RoadmapStep(
        id=2,
        name="Shape visualisations",
        description="Derive chart-friendly data series",
    ),
    RoadmapStep(
        id=3,
        name="Draft data report",
        description="Summarize key deltas in a FlowResponse",
    ),
    FINAL_STEP,
]


async def emit_status(
    ctx,
    *,
    status: Literal["thinking", "ok"] = "thinking",
    message: str | None = None,
    roadmap_step_list: list[RoadmapStep] | None = None,
    roadmap_step_id: int | None = None,
    roadmap_step_status: Literal["running", "ok"] | None = None,
) -> StatusUpdate:
    """Emit a websocket-friendly status update to the Rookery sink."""

    update = StatusUpdate(
        status=status,
        message=message,
        roadmap_step_list=roadmap_step_list,
        roadmap_step_id=roadmap_step_id,
        roadmap_step_status=roadmap_step_status,
    )
    runtime = ctx.runtime
    if runtime is None:
        raise RuntimeError("Context is not attached to a running flow")
    await runtime._emit_to_rookery(update, source=ctx.owner)
    return update


async def announce_start(message: Message, ctx) -> Message:
    await emit_status(ctx, message="Determining message path")
    message.meta.setdefault("context", {})
    return message


async def triage(message: Message, ctx) -> Message:
    query = UserQuery.model_validate(message.payload)
    message.meta.setdefault("context", {})
    message.meta["context"]["query"] = query.text
    message.meta["context"]["session_id"] = query.session_id

    lowered = query.text.lower()
    if any(token in lowered for token in ["error", "bug", "stacktrace", "traceback"]):
        payload = CodeAnalysisRequest(
            kind="code",
            query=query.text,
            files=["app.py", "payments.py"],
            session_id=query.session_id,
        )
        return message.model_copy(update={"payload": payload})

    payload = DataSummaryRequest(
        kind="data",
        query=query.text,
        tables=["daily_signups", "conversion_rate"],
        session_id=query.session_id,
    )
    return message.model_copy(update={"payload": payload})


async def code_plan(message: Message, ctx) -> Message:
    message.meta["roadmap"] = [step.model_dump() for step in CODE_STEPS]
    await emit_status(ctx, roadmap_step_list=CODE_STEPS)
    return message


async def code_parse_files(message: Message, ctx) -> Message:
    request = CodeAnalysisRequest.model_validate(message.payload)
    parsed = []
    for file_name in request.files:
        await emit_status(
            ctx,
            roadmap_step_id=1,
            roadmap_step_status="running",
            message=f"Parsing {file_name}",
        )
        await asyncio.sleep(0)
        parsed.append({"file": file_name, "tokens": 128})
    await emit_status(
        ctx,
        roadmap_step_id=1,
        roadmap_step_status="ok",
        message=f"Parsed {len(parsed)} files",
    )
    message.meta["parsed_files"] = parsed
    return message


async def code_inspect_modules(message: Message, ctx) -> Message:
    # request = CodeAnalysisRequest.model_validate(message.payload)
    parsed = message.meta.get("parsed_files", [])

    async def inspect(file_info: dict[str, Any]) -> dict[str, Any]:
        file_name = file_info["file"]
        await emit_status(
            ctx,
            roadmap_step_id=2,
            roadmap_step_status="running",
            message=f"Inspecting {file_name}",
        )
        await asyncio.sleep(0)
        return {"file": file_name, "issues": ["no obvious bugs"]}

    insights = await map_concurrent(parsed, inspect, max_concurrency=2)
    await emit_status(
        ctx,
        roadmap_step_id=2,
        roadmap_step_status="ok",
        message=f"Reviewed {len(insights)} modules",
    )
    message.meta["code_insights"] = insights
    message.meta.setdefault("context", {})["last_route"] = "code"
    return message


async def code_finalize(message: Message, ctx) -> Message:
    request = CodeAnalysisRequest.model_validate(message.payload)
    await emit_status(
        ctx,
        roadmap_step_id=3,
        roadmap_step_status="running",
        message="Summarizing code findings",
    )
    insights = message.meta.get("code_insights", [])
    summary_lines = [f"- {item['file']}: {item['issues'][0]}" for item in insights]
    summary = "\n".join(summary_lines) or "- No issues detected"
    response = FlowResponse(
        raw_output=f"Code analysis completed for {request.query}",
        artifacts={
            "insights": insights,
            "parsed_files": message.meta.get("parsed_files", []),
        },
        session_info=request.session_id,
    )
    message.meta["summary"] = summary
    await emit_status(
        ctx,
        roadmap_step_id=3,
        roadmap_step_status="ok",
        message="Drafted code report",
    )
    return message.model_copy(update={"payload": response})


async def data_plan(message: Message, ctx) -> Message:
    message.meta["roadmap"] = [step.model_dump() for step in DATA_STEPS]
    await emit_status(ctx, roadmap_step_list=DATA_STEPS)
    return message


async def data_collect_metrics(message: Message, ctx) -> Message:
    request = DataSummaryRequest.model_validate(message.payload)
    metrics = []
    for table in request.tables:
        await emit_status(
            ctx,
            roadmap_step_id=1,
            roadmap_step_status="running",
            message=f"Collecting metrics from {table}",
        )
        await asyncio.sleep(0)
        metrics.append({"table": table, "value": 42})
    await emit_status(
        ctx,
        roadmap_step_id=1,
        roadmap_step_status="ok",
        message=f"Collected {len(metrics)} metric sets",
    )
    message.meta["metrics"] = metrics
    message.meta.setdefault("context", {})["last_route"] = "data"
    return message


async def data_prepare_visuals(message: Message, ctx) -> Message:
    metrics = message.meta.get("metrics", [])
    await emit_status(
        ctx,
        roadmap_step_id=2,
        roadmap_step_status="running",
        message="Transforming metrics into chart data",
    )
    await asyncio.sleep(0)
    chart = {
        "series": [metric["value"] for metric in metrics],
        "labels": [metric["table"] for metric in metrics],
    }
    message.meta["chart"] = chart
    await emit_status(
        ctx,
        roadmap_step_id=2,
        roadmap_step_status="ok",
        message="Prepared chart inputs",
    )
    return message


async def data_finalize(message: Message, ctx) -> Message:
    request = DataSummaryRequest.model_validate(message.payload)
    await emit_status(
        ctx,
        roadmap_step_id=3,
        roadmap_step_status="running",
        message="Summarizing metric trends",
    )
    metrics = message.meta.get("metrics", [])
    chart = message.meta.get("chart", {})
    summary_lines = [f"- {m['table']}: {m['value']}" for m in metrics]
    summary = "\n".join(summary_lines) or "- No metrics available"
    response = FlowResponse(
        raw_output=f"Data summary ready for {request.query}",
        artifacts={"metrics": metrics, "chart": chart},
        session_info=request.session_id,
    )
    message.meta["summary"] = summary
    await emit_status(
        ctx,
        roadmap_step_id=3,
        roadmap_step_status="ok",
        message="Drafted data report",
    )
    return message.model_copy(update={"payload": response})


async def synthesize_answer(message: Message, ctx) -> Message:
    await emit_status(
        ctx,
        roadmap_step_id=FINAL_STEP.id,
        roadmap_step_status="running",
        message="Synthesizing final response",
    )
    response = FlowResponse.model_validate(message.payload)
    summary = message.meta.get("summary", "")
    context = message.meta.get("context", {})
    final_text = (
        "Final reply for session {session}:\nSummary:\n{summary}\n\n{raw_output}"
    ).format(
        session=context.get("session_id", "unknown"),
        summary=summary,
        raw_output=response.raw_output,
    )
    await ctx.emit_chunk(parent=message, text="Synthesizing final reply... ")
    await ctx.emit_chunk(parent=message, text="Done composing.", done=True)
    await emit_status(
        ctx,
        roadmap_step_id=FINAL_STEP.id,
        roadmap_step_status="ok",
        message="Done!",
    )
    meta = dict(message.meta)
    meta["flow_response"] = response.model_dump()
    return message.model_copy(update={"payload": final_text, "meta": meta})


def build_flow() -> PenguiFlow:
    """Construct the roadmap flow with routing and synthesis."""

    announce_node = Node(
        announce_start, name="announce_start", policy=NodePolicy(validate="none")
    )
    triage_node = Node(triage, name="triage", policy=NodePolicy(validate="none"))

    def choose_branch(msg: Message) -> str:
        payload = msg.payload
        if isinstance(payload, CodeAnalysisRequest):
            return "code_plan"
        if isinstance(payload, DataSummaryRequest):
            return "data_plan"
        raise TypeError(f"Unsupported payload type: {type(payload)!r}")

    dispatcher = predicate_router("dispatcher", choose_branch)

    code_plan_node = Node(
        code_plan, name="code_plan", policy=NodePolicy(validate="none")
    )
    code_parse_node = Node(
        code_parse_files, name="code_parse", policy=NodePolicy(validate="none")
    )
    code_inspect_node = Node(
        code_inspect_modules, name="code_inspect", policy=NodePolicy(validate="none")
    )
    code_finalize_node = Node(
        code_finalize, name="code_finalize", policy=NodePolicy(validate="none")
    )

    data_plan_node = Node(
        data_plan, name="data_plan", policy=NodePolicy(validate="none")
    )
    data_collect_node = Node(
        data_collect_metrics, name="data_collect", policy=NodePolicy(validate="none")
    )
    data_prepare_node = Node(
        data_prepare_visuals, name="data_prepare", policy=NodePolicy(validate="none")
    )
    data_finalize_node = Node(
        data_finalize, name="data_finalize", policy=NodePolicy(validate="none")
    )

    synthesize_node = Node(
        synthesize_answer, name="synthesize", policy=NodePolicy(validate="none")
    )

    flow = create(
        announce_node.to(triage_node),
        triage_node.to(dispatcher),
        dispatcher.to(code_plan_node, data_plan_node),
        code_plan_node.to(code_parse_node),
        code_parse_node.to(code_inspect_node),
        code_inspect_node.to(code_finalize_node),
        data_plan_node.to(data_collect_node),
        data_collect_node.to(data_prepare_node),
        data_prepare_node.to(data_finalize_node),
        code_finalize_node.to(synthesize_node),
        data_finalize_node.to(synthesize_node),
        synthesize_node.to(),
    )
    return flow


def mermaid_diagram(direction: str = "TD") -> str:
    """Render the flow as a Mermaid graph."""

    flow = build_flow()
    return flow_to_mermaid(flow, direction=direction)


async def run_demo() -> None:
    """Run the flow end-to-end and print status updates."""

    flow = build_flow()
    flow.run()
    try:
        message = Message(
            payload=UserQuery(
                text="Investigate the checkout bug", session_id="session-123"
            ),
            headers=Headers(tenant="demo"),
        )
        await flow.emit(message)

        finished = False
        while not finished:
            result = await flow.fetch()
            if isinstance(result, StatusUpdate):
                print(f"[status] {result.model_dump()}")
            elif isinstance(result, Message):
                payload = result.payload
                if isinstance(payload, StreamChunk):
                    print(f"[chunk] {payload.text} (done={payload.done})")
                else:
                    print(f"[final] {payload}")
                    finished = True
            else:
                print(f"[event] {result}")
    finally:
        await flow.stop()


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    asyncio.run(run_demo())
