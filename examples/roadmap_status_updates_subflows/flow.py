from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from penguiflow import (
    FinalAnswer,
    Headers,
    Message,
    ModelRegistry,
    Node,
    NodePolicy,
    PenguiFlow,
    StreamChunk,
    create,
    flow_to_mermaid,
    join_k,
    map_concurrent,
)


class UserQuery(BaseModel):
    """Incoming query payload from the frontend."""

    text: str


class RoadmapStep(BaseModel):
    """Describes an item in the UI roadmap."""

    id: int
    name: str
    description: str


class StatusUpdate(BaseModel):
    """UI status message emitted through the websocket."""

    status: Literal["thinking", "ok", "error"]
    message: str | None = None
    roadmap_step_list: list[RoadmapStep] | None = None
    roadmap_step_id: int | None = None
    roadmap_step_status: Literal["running", "ok", "error"] | None = None


class FlowResponse(BaseModel):
    """Pydantic model for Flow response structure."""

    raw_output: str
    artifacts: dict[str, Any] | None = None
    session_info: str | None = None


class RouteDecision(BaseModel):
    """Selected branch for the query."""

    query: UserQuery
    route: Literal["documents", "bug"]
    reason: str


class DocumentState(BaseModel):
    """Mutable state for the document analysis branch."""

    query: UserQuery
    route: Literal["documents"] = "documents"
    steps: list[RoadmapStep]
    sources: list[str] = Field(default_factory=list)
    metadata: list[str] = Field(default_factory=list)
    summary: str | None = None


class BugState(BaseModel):
    """Mutable state for the bug triage branch."""

    query: UserQuery
    route: Literal["bug"] = "bug"
    steps: list[RoadmapStep]
    logs: list[str] = Field(default_factory=list)
    checks: dict[str, str] = Field(default_factory=dict)
    diagnosis: str | None = None


class DiagnosticTask(BaseModel):
    """Work item forwarded to diagnostics subflows."""

    state: BugState
    check_name: str
    detail: str
    outcome: Literal["pass", "fail"] | None = None


class DiagnosticBatch(BaseModel):
    """Aggregated diagnostics returned by join_k."""

    tasks: list[DiagnosticTask]


class SynthesisInput(BaseModel):
    """Payload handed to the final synthesis node."""

    query: UserQuery
    route: Literal["documents", "bug"]
    steps: list[RoadmapStep]
    subflow_response: FlowResponse


FINAL_STEP = RoadmapStep(
    id=99,
    name="Compose final answer",
    description="Merge context and model output for the UI",
)

DOCUMENT_STEPS: list[RoadmapStep] = [
    RoadmapStep(id=1, name="Parse files", description="Enumerate candidate documents"),
    RoadmapStep(id=2, name="Extract metadata", description="Analyze files in parallel"),
    RoadmapStep(
        id=3, name="Generate summary", description="Produce branch summary text"
    ),
    RoadmapStep(
        id=4, name="Render HTML report", description="Attach structured artifacts"
    ),
    FINAL_STEP,
]

BUG_STEPS: list[RoadmapStep] = [
    RoadmapStep(id=10, name="Collect error logs", description="Gather stack traces"),
    RoadmapStep(
        id=11, name="Reproduce failure", description="Run lightweight diagnostics"
    ),
    RoadmapStep(id=12, name="Outline fix", description="Summarize remediation plan"),
    FINAL_STEP,
]

STATUS_BUFFER: defaultdict[str, list[StatusUpdate]] = defaultdict(list)
CHUNK_BUFFER: defaultdict[str, list[StreamChunk]] = defaultdict(list)


def reset_buffers() -> None:
    """Helper used by tests to clear captured telemetry."""

    STATUS_BUFFER.clear()
    CHUNK_BUFFER.clear()


def _find_target(ctx, target_name: str) -> Node | None:
    for candidate in getattr(ctx, "_outgoing", {}):
        if getattr(candidate, "name", None) == target_name:
            return candidate
    return None


async def _emit_to_successors(
    ctx,
    parent: Message,
    payload: Any,
    *,
    extra_exclude: set[str] | None = None,
) -> None:
    exclude = {"status_updates"}
    if extra_exclude:
        exclude.update(extra_exclude)

    for candidate in getattr(ctx, "_outgoing", {}):
        name = getattr(candidate, "name", None)
        if name in exclude:
            continue
        next_message = parent.model_copy(update={"payload": payload})
        await ctx.emit(next_message, to=candidate)


async def _emit_to_target(ctx, parent: Message, payload: Any, target_name: str) -> None:
    target = _find_target(ctx, target_name)
    if target is None:  # pragma: no cover - defensive guard for misconfigured graphs
        raise RuntimeError(
            f"{target_name} is not connected to {getattr(ctx.owner, 'name', ctx.owner)}"
        )
    next_message = parent.model_copy(update={"payload": payload})
    await ctx.emit(next_message, to=target)


async def emit_status(
    ctx,
    parent: Message,
    *,
    status: Literal["thinking", "ok", "error"] = "thinking",
    message: str | None = None,
    roadmap_step_id: int | None = None,
    roadmap_step_status: Literal["running", "ok", "error"] | None = None,
    roadmap_step_list: list[RoadmapStep] | None = None,
) -> None:
    """Fan-out helper that pushes a :class:`StatusUpdate` to the status sink."""

    update = StatusUpdate(
        status=status,
        message=message,
        roadmap_step_id=roadmap_step_id,
        roadmap_step_status=roadmap_step_status,
        roadmap_step_list=roadmap_step_list,
    )
    STATUS_BUFFER[parent.trace_id].append(update)
    status_message = parent.model_copy(update={"payload": update})
    target = _find_target(ctx, "status_updates")
    if target is None:  # pragma: no cover - defensive guard for misconfigured graphs
        raise RuntimeError("status_updates node is not connected to this context")
    await ctx.emit(status_message, to=target)


async def status_collector(message: Message, _ctx) -> None:
    return None


def build_metadata_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    """Create a subflow that enriches document metadata concurrently."""

    async def compute_metadata(message: Message, _ctx) -> DocumentState:
        state = message.payload
        if not isinstance(state, DocumentState):
            raise TypeError("metadata_mapper expects a DocumentState payload")

        async def analyse(source: str) -> str:
            await asyncio.sleep(0.01)
            checksum = sum(ord(char) for char in source) % 97
            return f"{source}:tokens={len(source)}:digest={checksum}"

        metadata = await map_concurrent(state.sources, analyse, max_concurrency=2)
        return state.model_copy(update={"metadata": metadata})

    metadata_node = Node(
        compute_metadata, name="metadata_mapper", policy=NodePolicy(validate="none")
    )
    flow = create(metadata_node.to())
    registry = ModelRegistry()
    registry.register("metadata_mapper", DocumentState, DocumentState)
    return flow, registry


def build_diagnostics_playbook() -> tuple[PenguiFlow, ModelRegistry]:
    """Create a subflow that fans out diagnostics and joins them with ``join_k``."""

    async def seed_checks(message: Message, ctx) -> None:
        state = message.payload
        if not isinstance(state, BugState):
            raise TypeError("seed_checks expects a BugState payload")

        unit_target = _find_target(ctx, "unit_runner")
        integration_target = _find_target(ctx, "integration_runner")
        if unit_target is None or integration_target is None:
            raise RuntimeError(
                "diagnostics playbook requires unit and integration runners"
            )

        tasks = [
            DiagnosticTask(
                state=state, check_name="unit", detail="Unit regression suite"
            ),
            DiagnosticTask(
                state=state,
                check_name="integration",
                detail="Integration smoke tests",
            ),
        ]

        for task in tasks:
            target = unit_target if task.check_name == "unit" else integration_target
            await ctx.emit(
                message.model_copy(update={"payload": task}), to=target
            )

    async def run_unit_check(message: Message, ctx) -> None:
        task = message.payload
        if not isinstance(task, DiagnosticTask):
            raise TypeError("run_unit_check expects DiagnosticTask payloads")

        join_target = _find_target(ctx, "join_diagnostics")
        if join_target is None:
            raise RuntimeError("diagnostics playbook missing join_diagnostics node")

        updated = task.model_copy(
            update={"outcome": "pass", "detail": f"{task.detail} :: ok"}
        )
        await ctx.emit(
            message.model_copy(update={"payload": updated}), to=join_target
        )

    async def run_integration_check(message: Message, ctx) -> None:
        task = message.payload
        if not isinstance(task, DiagnosticTask):
            raise TypeError("run_integration_check expects DiagnosticTask payloads")

        join_target = _find_target(ctx, "join_diagnostics")
        if join_target is None:
            raise RuntimeError("diagnostics playbook missing join_diagnostics node")

        updated = task.model_copy(
            update={"outcome": "fail", "detail": f"{task.detail} :: incident"}
        )
        await ctx.emit(
            message.model_copy(update={"payload": updated}), to=join_target
        )

    async def shape_batch(message: Message, _ctx) -> Message:
        tasks = message.payload
        if not isinstance(tasks, list):
            raise TypeError("format_diagnostic_batch expects a list payload")
        batch = DiagnosticBatch(
            tasks=[DiagnosticTask.model_validate(task) for task in tasks]
        )
        return message.model_copy(update={"payload": batch})

    async def merge_batch(message: Message, _ctx) -> BugState:
        batch = message.payload
        if not isinstance(batch, DiagnosticBatch):
            raise TypeError("merge_diagnostics expects a DiagnosticBatch payload")
        if not batch.tasks:
            raise ValueError("merge_diagnostics received an empty batch")

        base_state = batch.tasks[0].state
        checks = {task.check_name: task.outcome or "unknown" for task in batch.tasks}
        log_entries = [
            f"{task.check_name}: {task.detail} ({task.outcome or 'unknown'})"
            for task in batch.tasks
        ]
        updated_logs = [*base_state.logs, *log_entries]
        return base_state.model_copy(update={"checks": checks, "logs": updated_logs})

    seed_node = Node(
        seed_checks, name="seed_checks", policy=NodePolicy(validate="none")
    )
    unit_node = Node(
        run_unit_check, name="unit_runner", policy=NodePolicy(validate="none")
    )
    integration_node = Node(
        run_integration_check,
        name="integration_runner",
        policy=NodePolicy(validate="none"),
    )
    join_node = join_k("join_diagnostics", 2)
    batch_node = Node(
        shape_batch,
        name="format_diagnostic_batch",
        policy=NodePolicy(validate="none"),
    )
    merge_node = Node(
        merge_batch, name="merge_diagnostics", policy=NodePolicy(validate="none")
    )

    flow = create(
        seed_node.to(unit_node, integration_node),
        unit_node.to(join_node),
        integration_node.to(join_node),
        join_node.to(batch_node),
        batch_node.to(merge_node),
        merge_node.to(),
    )

    registry = ModelRegistry()
    registry.register("merge_diagnostics", DiagnosticBatch, BugState)
    return flow, registry


async def chunk_collector(message: Message, _ctx) -> None:
    chunk = message.payload
    if isinstance(chunk, StreamChunk):
        CHUNK_BUFFER[message.trace_id].append(chunk)


async def announce_start(message: Message, ctx) -> None:
    await emit_status(ctx, message, message="Determining message path")
    await _emit_to_successors(ctx, message, message.payload)


async def triage(message: Message, ctx) -> None:
    payload = message.payload
    if not isinstance(payload, UserQuery):
        raise TypeError("triage expects a UserQuery payload")

    text = payload.text.lower()
    if any(keyword in text for keyword in ("bug", "error", "stacktrace")):
        route: Literal["documents", "bug"] = "bug"
        reason = "Detected incident keywords"
    else:
        route = "documents"
        reason = "Defaulted to document summarizer"

    await emit_status(ctx, message, message=f"Routing to {route} subflow")

    decision = RouteDecision(query=payload, route=route, reason=reason)
    target = "documents_plan" if route == "documents" else "bug_plan"
    await _emit_to_target(ctx, message, decision, target)


async def document_plan(message: Message, ctx) -> None:
    decision = message.payload
    assert isinstance(decision, RouteDecision) and decision.route == "documents"

    await emit_status(ctx, message, roadmap_step_list=DOCUMENT_STEPS)
    state = DocumentState(query=decision.query, steps=DOCUMENT_STEPS)
    await _emit_to_successors(ctx, message, state)


async def parse_documents(message: Message, ctx) -> None:
    state = message.payload
    assert isinstance(state, DocumentState)

    step = DOCUMENT_STEPS[0]
    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="running",
        message="Parsing repository sources",
    )

    sources = ["README.md", "metrics.md", "changelog.md"]
    updated = state.model_copy(update={"sources": sources})

    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="ok",
        message="Done!",
    )
    await _emit_to_successors(ctx, message, updated)


async def extract_metadata(message: Message, ctx) -> None:
    state = message.payload
    assert isinstance(state, DocumentState)

    step = DOCUMENT_STEPS[1]
    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="running",
        message="Launching metadata subflow",
    )

    updated_state = await ctx.call_playbook(build_metadata_playbook, message)
    if not isinstance(updated_state, DocumentState):
        raise TypeError("metadata subflow must return a DocumentState payload")
    updated = updated_state

    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="ok",
        message="Done!",
    )
    await _emit_to_successors(ctx, message, updated)


async def generate_summary(message: Message, ctx) -> None:
    state = message.payload
    assert isinstance(state, DocumentState)

    step = DOCUMENT_STEPS[2]
    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="running",
        message="Summarizing findings",
    )

    summary = f"Summarized {len(state.sources)} files with {len(state.metadata)}."
    updated = state.model_copy(update={"summary": summary})

    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="ok",
        message="Done!",
    )
    await _emit_to_successors(ctx, message, updated)


async def render_report(message: Message, ctx) -> None:
    state = message.payload
    assert isinstance(state, DocumentState)

    step = DOCUMENT_STEPS[3]
    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="running",
        message="Assembling HTML report",
    )

    artifacts = {
        "sources": state.sources,
        "metadata": state.metadata,
    }
    subflow_response = FlowResponse(
        raw_output=state.summary or "No summary available",
        artifacts=artifacts,
        session_info="documents-branch",
    )

    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="ok",
        message="Done!",
    )
    payload = SynthesisInput(
        query=state.query,
        route="documents",
        steps=state.steps,
        subflow_response=subflow_response,
    )
    await _emit_to_successors(ctx, message, payload)


async def bug_plan(message: Message, ctx) -> None:
    decision = message.payload
    assert isinstance(decision, RouteDecision) and decision.route == "bug"

    await emit_status(ctx, message, roadmap_step_list=BUG_STEPS)
    state = BugState(query=decision.query, steps=BUG_STEPS)
    await _emit_to_successors(ctx, message, state)


async def collect_logs(message: Message, ctx) -> None:
    state = message.payload
    assert isinstance(state, BugState)

    step = BUG_STEPS[0]
    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="running",
        message="Collecting stack traces",
    )

    logs = ["ValueError: invalid status", "Traceback (most recent call last)"]
    updated = state.model_copy(update={"logs": logs})

    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="ok",
        message="Done!",
    )
    await _emit_to_successors(ctx, message, updated)


async def run_diagnostics(message: Message, ctx) -> None:
    state = message.payload
    assert isinstance(state, BugState)

    step = BUG_STEPS[1]
    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="running",
        message="Launching diagnostics subflow",
    )

    updated_state = await ctx.call_playbook(build_diagnostics_playbook, message)
    if not isinstance(updated_state, BugState):
        raise TypeError("diagnostics subflow must return a BugState payload")
    updated = updated_state

    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="ok",
        message="Done!",
    )
    await _emit_to_successors(ctx, message, updated)


async def propose_fix(message: Message, ctx) -> None:
    state = message.payload
    assert isinstance(state, BugState)

    step = BUG_STEPS[2]
    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="running",
        message="Drafting fix recommendations",
    )

    diagnosis = "Integration regression detected. Roll back deployment."
    # updated = state.model_copy(update={"diagnosis": diagnosis})

    subflow_response = FlowResponse(
        raw_output=diagnosis,
        artifacts={"logs": state.logs, "checks": state.checks},
        session_info="bug-branch",
    )

    await emit_status(
        ctx,
        message,
        roadmap_step_id=step.id,
        roadmap_step_status="ok",
        message="Done!",
    )
    payload = SynthesisInput(
        query=state.query,
        route="bug",
        steps=state.steps,
        subflow_response=subflow_response,
    )
    await _emit_to_successors(ctx, message, payload)


async def compose_final(message: Message, ctx) -> None:
    payload = message.payload
    assert isinstance(payload, SynthesisInput)

    final_step = payload.steps[-1]
    await emit_status(
        ctx,
        message,
        roadmap_step_id=final_step.id,
        roadmap_step_status="running",
        message="Synthesizing final response",
    )

    chunk_target = _find_target(ctx, "chunk_sink")
    if chunk_target is not None:
        await ctx.emit_chunk(
            parent=message,
            text="Synthesizing insights... ",
            meta={"phase": "compose", "stage": 1},
            to=chunk_target,
        )
        await ctx.emit_chunk(
            parent=message,
            text="ready.",
            meta={"phase": "compose", "stage": 2},
            done=True,
            to=chunk_target,
        )

    raw_output = f"{payload.subflow_response.raw_output}\n\nRoute: {payload.route}."
    artifacts = dict(payload.subflow_response.artifacts or {})
    artifacts.setdefault("route", payload.route)

    final_response = FlowResponse(
        raw_output=raw_output,
        artifacts=artifacts,
        session_info=f"steps={len(payload.steps)}",
    )

    await emit_status(
        ctx,
        message,
        roadmap_step_id=final_step.id,
        roadmap_step_status="ok",
        message="Done!",
    )

    meta = dict(message.meta)
    meta["flow_response"] = final_response.model_dump()
    target = _find_target(ctx, "deliver_final")
    if target is None:  # pragma: no cover - defensive guard
        raise RuntimeError("deliver_final node is not connected")
    response_message = message.model_copy(
        update={"payload": final_response, "meta": meta}
    )
    await ctx.emit(response_message, to=target)


async def deliver_final(message: Message, _ctx) -> FinalAnswer:
    payload = message.payload
    assert isinstance(payload, FlowResponse)

    text = payload.raw_output
    if payload.artifacts:
        text += f"\nArtifacts: {sorted(payload.artifacts)}"

    final_answer = FinalAnswer(text=text)
    return final_answer


def build_flow() -> tuple[PenguiFlow, ModelRegistry]:
    status_node = Node(
        status_collector, name="status_updates", policy=NodePolicy(validate="none")
    )
    chunk_node = Node(
        chunk_collector, name="chunk_sink", policy=NodePolicy(validate="none")
    )

    start_node = Node(announce_start, name="start", policy=NodePolicy(validate="none"))
    triage_node = Node(triage, name="triage", policy=NodePolicy(validate="none"))

    doc_plan_node = Node(
        document_plan, name="documents_plan", policy=NodePolicy(validate="none")
    )
    parse_node = Node(
        parse_documents, name="parse_documents", policy=NodePolicy(validate="none")
    )
    metadata_node = Node(
        extract_metadata, name="extract_metadata", policy=NodePolicy(validate="none")
    )
    summary_node = Node(
        generate_summary, name="generate_summary", policy=NodePolicy(validate="none")
    )
    render_node = Node(
        render_report, name="render_report", policy=NodePolicy(validate="none")
    )

    bug_plan_node = Node(bug_plan, name="bug_plan", policy=NodePolicy(validate="none"))
    logs_node = Node(
        collect_logs, name="collect_logs", policy=NodePolicy(validate="none")
    )
    diagnostics_node = Node(
        run_diagnostics, name="run_diagnostics", policy=NodePolicy(validate="none")
    )
    fix_node = Node(propose_fix, name="propose_fix", policy=NodePolicy(validate="none"))

    compose_node = Node(
        compose_final, name="compose_final", policy=NodePolicy(validate="none")
    )
    final_node = Node(
        deliver_final, name="deliver_final", policy=NodePolicy(validate="none")
    )

    flow = create(
        start_node.to(triage_node, status_node),
        triage_node.to(doc_plan_node, bug_plan_node, status_node),
        doc_plan_node.to(parse_node, status_node),
        parse_node.to(metadata_node, status_node),
        metadata_node.to(summary_node, status_node),
        summary_node.to(render_node, status_node),
        render_node.to(compose_node, status_node),
        bug_plan_node.to(logs_node, status_node),
        logs_node.to(diagnostics_node, status_node),
        diagnostics_node.to(fix_node, status_node),
        fix_node.to(compose_node, status_node),
        compose_node.to(status_node, chunk_node, final_node),
        status_node.to(),
        chunk_node.to(),
        final_node.to(),
    )

    registry = ModelRegistry()
    registry.register("documents_plan", RouteDecision, DocumentState)
    registry.register("parse_documents", DocumentState, DocumentState)
    registry.register("extract_metadata", DocumentState, DocumentState)
    registry.register("generate_summary", DocumentState, DocumentState)
    registry.register("render_report", DocumentState, SynthesisInput)
    registry.register("bug_plan", RouteDecision, BugState)
    registry.register("collect_logs", BugState, BugState)
    registry.register("run_diagnostics", BugState, BugState)
    registry.register("propose_fix", BugState, SynthesisInput)
    registry.register("compose_final", SynthesisInput, FlowResponse)
    registry.register("deliver_final", FlowResponse, FinalAnswer)

    return flow, registry


async def run_example(query: str) -> FinalAnswer:
    reset_buffers()
    flow, registry = build_flow()
    flow.run(registry=registry)
    try:
        message = Message(payload=UserQuery(text=query), headers=Headers(tenant="demo"))
        await flow.emit(message)
        result = await flow.fetch()
        assert isinstance(result, FinalAnswer)
        return result
    finally:
        await flow.stop()


def export_mermaid(flow: PenguiFlow, destination: Path | None = None) -> Path:
    mermaid = flow_to_mermaid(flow, direction="TD")
    path = destination or Path(__file__).with_name("flow.mermaid.md")
    path.write_text(f"```mermaid\n{mermaid}\n```\n")
    return path


async def main() -> None:  # pragma: no cover - manual entrypoint
    answer = await run_example("Summarize the latest release notes")

    print("\n=== ROOKERY STATUS UPDATES ===")
    for trace_id, updates in STATUS_BUFFER.items():
        print(f"\nTrace: {trace_id}")
        for i, update in enumerate(updates, 1):
            print(f"  [{i}] {update.model_dump_json(indent=2)}")

    print("\n=== ROOKERY STREAM CHUNKS ===")
    for trace_id, chunks in CHUNK_BUFFER.items():
        print(f"\nTrace: {trace_id}")
        for i, chunk in enumerate(chunks, 1):
            print(f"  [{i}] {chunk.model_dump_json(indent=2)}")

    print("\n=== FINAL ANSWER ===")
    print(answer.text)


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    asyncio.run(main())
