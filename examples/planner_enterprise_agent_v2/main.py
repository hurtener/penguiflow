"""Enterprise agent orchestrator with ReactPlanner and comprehensive observability.

This is the cornerstone implementation for production agent deployments,
demonstrating:
- ReactPlanner integration with auto-discovered nodes
- Telemetry middleware for full error visibility
- Status update sinks for frontend integration
- Streaming support for progressive UI updates
- Environment-based configuration
- Enterprise-grade error handling
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import defaultdict
from typing import Any
from uuid import uuid4

from examples.planner_enterprise_agent_v2.config import AgentConfig
from examples.planner_enterprise_agent_v2.nodes import (
    FinalAnswer,
    StatusUpdate,
    UserQuery,
    analyze_documents_pipeline,  # Pattern A: Wrapped subflow
    answer_general_query,
    collect_error_logs,  # Pattern B: Individual node
    initialize_bug_workflow,  # Pattern B: Individual node
    recommend_bug_fix,  # Pattern B: Individual node
    run_diagnostics,  # Pattern B: Individual node
    triage_query,
)
from examples.planner_enterprise_agent_v2.telemetry import AgentTelemetry
from penguiflow.catalog import build_catalog
from penguiflow.node import Node
from penguiflow.planner import (
    DSPyLLMClient,
    PlannerPause,
    ReactPlanner,
    ReflectionConfig,
    ReflectionCriteria,
    ToolPolicy,
)
from penguiflow.registry import ModelRegistry

# Global buffers for demonstration (in production: use message queue/websocket)
STATUS_BUFFER: defaultdict[str, list[StatusUpdate]] = defaultdict(list)
EXECUTION_LOGS: list[str] = []


class EnterpriseAgentOrchestrator:
    """Production-ready agent orchestrator with ReactPlanner.

    This orchestrator demonstrates enterprise deployment patterns:
    - Injectable telemetry for testing and monitoring
    - Middleware integration for error visibility
    - Event callback for planner observability
    - Clean separation of concerns
    - Graceful degradation and error handling

    Thread Safety:
        NOT thread-safe. Create separate instances per request/session.

    Example:
        config = AgentConfig.from_env()
        agent = EnterpriseAgentOrchestrator(config)
        result = await agent.execute("Analyze recent deployment logs")
    """

    def __init__(
        self,
        config: AgentConfig,
        *,
        telemetry: AgentTelemetry | None = None,
    ) -> None:
        self.config = config
        self.telemetry = telemetry or AgentTelemetry(config)

        # Configure logging
        logging.basicConfig(
            level=getattr(logging, config.log_level),
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )

        # Build node registry
        self._nodes = self._build_nodes()
        self._registry = self._build_registry()

        # Build planner with telemetry
        self._planner = self._build_planner()

        self.telemetry.logger.info(
            "orchestrator_initialized",
            extra={
                "environment": config.environment,
                "agent_name": config.agent_name,
                "node_count": len(self._nodes),
            },
        )

    def _build_nodes(self) -> list[Node]:
        """Construct all planner-discoverable nodes.

        This demonstrates TWO patterns for organizing workflows:

        Pattern A - Wrapped Subflow (Document Analysis):
            The entire document workflow is wrapped as a single tool that
            internally executes a 5-node subflow. The planner sees ONE tool:
            "analyze_documents_pipeline" that handles everything from parsing
            to final report generation.

            Benefits:
            - Simpler for planner (1 tool vs 5 nodes)
            - Lower LLM cost (1 decision vs 5 decisions)
            - Better for deterministic pipelines
            - Abstraction and reusability

        Pattern B - Individual Nodes (Bug Triage):
            Each step is exposed as a separate planner-discoverable node.
            The planner can dynamically decide whether to collect logs,
            run diagnostics, or skip steps based on context.

            Benefits:
            - Maximum flexibility and control
            - Planner can adapt mid-workflow
            - Better observability of each step
            - Enables conditional/parallel execution

        Use Pattern A when workflows are deterministic and linear.
        Use Pattern B when workflows need dynamic decision-making.
        """
        return [
            # Router (always individual)
            Node(triage_query, name="triage_query"),
            # PATTERN A: Document workflow as wrapped subflow
            Node(analyze_documents_pipeline, name="analyze_documents"),
            # PATTERN B: Bug workflow as individual nodes
            Node(initialize_bug_workflow, name="init_bug"),
            Node(collect_error_logs, name="collect_logs"),
            Node(run_diagnostics, name="run_diagnostics"),
            Node(recommend_bug_fix, name="recommend_fix"),
            # General (individual node)
            Node(answer_general_query, name="answer_general"),
        ]

    def _build_registry(self) -> ModelRegistry:
        """Register all type mappings for validation.

        Note the difference in registrations:

        Pattern A (Wrapped Subflow):
            analyze_documents: RouteDecision → FinalAnswer
            The planner sees this as a single-step transformation.
            Internally it executes 5 nodes, but externally it's one tool.

        Pattern B (Individual Nodes):
            init_bug: RouteDecision → BugState
            collect_logs: BugState → BugState
            run_diagnostics: BugState → BugState
            recommend_fix: BugState → FinalAnswer
            The planner sees each step and can make decisions between them.
        """
        registry = ModelRegistry()

        # Import types
        from examples.planner_enterprise_agent_v2.nodes import (
            BugState,
            RouteDecision,
        )

        # Router
        registry.register("triage_query", UserQuery, RouteDecision)

        # PATTERN A: Document workflow as wrapped subflow
        # Single registration: RouteDecision → FinalAnswer
        # (Internal subflow nodes are NOT registered in planner catalog)
        registry.register("analyze_documents", RouteDecision, FinalAnswer)

        # PATTERN B: Bug workflow as individual nodes
        # Each step registered separately for granular control
        registry.register("init_bug", RouteDecision, BugState)
        registry.register("collect_logs", BugState, BugState)
        registry.register("run_diagnostics", BugState, BugState)
        registry.register("recommend_fix", BugState, FinalAnswer)

        # General
        registry.register("answer_general", RouteDecision, FinalAnswer)

        return registry

    def _build_planner(self) -> ReactPlanner:
        """Construct ReactPlanner with enterprise configuration (v2 - Enhanced).

        V2 Enhancements:
        - Reflection loop for answer quality assurance (FLAGSHIP)
        - Tool policy for runtime access control
        - Planning hints for workflow constraints
        - State store for durable pause/resume
        - Separate LLMs for reflection and summarization
        """
        catalog = build_catalog(self._nodes, self._registry)

        # Use DSPy for better structured output handling across providers
        # DSPy is especially beneficial for models that don't support native
        # JSON schema mode (like Databricks, Cerebras), but works well with all providers
        # Can be explicitly enabled via DSPY_CLIENT=true env var
        use_dspy = self.config.use_dspy_client or self.config.llm_model.startswith("databricks/")

        # Configure LLM client
        llm_client = None
        if use_dspy:
            llm_client = DSPyLLMClient(
                llm=self.config.llm_model,
                temperature=self.config.llm_temperature,
                max_retries=self.config.llm_max_retries,
                timeout_s=self.config.llm_timeout_s,
                max_tokens=self.config.llm_max_tokens,
            )
            self.telemetry.logger.info(
                "using_dspy_client",
                extra={"model": self.config.llm_model},
            )

        # V2: Configure reflection loop (FLAGSHIP FEATURE)
        reflection_config = None
        reflection_llm = None
        if self.config.reflection_enabled:
            reflection_config = ReflectionConfig(
                enabled=True,
                criteria=ReflectionCriteria(
                    completeness="Fully addresses all parts of the user's query",
                    accuracy="Factually correct based on observations and retrieved data",
                    clarity="Well-structured, clear, and easy to understand",
                ),
                quality_threshold=self.config.reflection_quality_threshold,
                max_revisions=self.config.reflection_max_revisions,
                use_separate_llm=self.config.reflection_use_separate_llm,
            )
            if self.config.reflection_use_separate_llm and self.config.reflection_llm:
                reflection_llm = self.config.reflection_llm
            self.telemetry.logger.info(
                "reflection_enabled",
                extra={
                    "quality_threshold": self.config.reflection_quality_threshold,
                    "max_revisions": self.config.reflection_max_revisions,
                    "separate_llm": self.config.reflection_use_separate_llm,
                    "reflection_model": reflection_llm or self.config.llm_model,
                },
            )

        # V2: Configure tool policy for runtime access control
        tool_policy = None
        if self.config.tool_policy_enabled:
            tool_policy = ToolPolicy(
                allowed_tools=self.config.tool_policy_allowed_tools,
                denied_tools=self.config.tool_policy_denied_tools,
                require_tags=self.config.tool_policy_require_tags,
            )
            self.telemetry.logger.info(
                "tool_policy_enabled",
                extra={
                    "allowed_tools": (
                        list(self.config.tool_policy_allowed_tools) if self.config.tool_policy_allowed_tools else None
                    ),
                    "denied_tools": list(self.config.tool_policy_denied_tools),
                    "require_tags": list(self.config.tool_policy_require_tags),
                },
            )

        # V2: Configure planning hints for workflow constraints
        planning_hints = None
        if self.config.planning_hints_enabled and self.config.planning_hints:
            planning_hints = self.config.planning_hints
            self.telemetry.logger.info(
                "planning_hints_enabled",
                extra={"hints": planning_hints},
            )

        # V2: Configure state store for durable pause/resume
        state_store = None
        if self.config.state_store_enabled:
            # In production, use Redis, SQLite, or other durable backend
            # For now, we'll pass None to use in-memory storage
            self.telemetry.logger.info(
                "state_store_enabled",
                extra={"backend": self.config.state_store_backend},
            )

        # Document context structure for LLM
        # This demonstrates how developers can use system_prompt_extra to document
        # their custom context format (memories, etc.)
        system_prompt_extra = """
## Context Usage

The user message may include a 'context' field with additional information:

- **memories**: Array of conversation history and user preferences
  - Format: `[{role: "user"|"assistant"|"system", content: str, timestamp?: str, metadata?: dict}]`
  - Use memories to provide context-aware responses
  - Reference previous interactions when relevant
  - Respect user preferences indicated in system-role memories

- **status_history**: Real-time execution progress updates
  - Contains roadmap steps and current status
  - Can help track multi-step workflow progress

When context is provided, use it appropriately to enhance your responses.
"""

        # CRITICAL: Set event_callback for planner observability
        planner = ReactPlanner(
            llm=self.config.llm_model,
            catalog=catalog,
            max_iters=self.config.planner_max_iters,
            temperature=self.config.llm_temperature,
            json_schema_mode=True if not use_dspy else False,
            llm_client=llm_client,
            system_prompt_extra=system_prompt_extra,
            token_budget=self.config.planner_token_budget,
            deadline_s=self.config.planner_deadline_s,
            hop_budget=self.config.planner_hop_budget,
            summarizer_llm=self.config.summarizer_model,
            llm_timeout_s=self.config.llm_timeout_s,
            llm_max_retries=self.config.llm_max_retries,
            absolute_max_parallel=self.config.planner_absolute_max_parallel,
            repair_attempts=self.config.planner_repair_attempts,
            # V2: New features
            reflection_config=reflection_config,
            reflection_llm=reflection_llm,
            tool_policy=tool_policy,
            planning_hints=planning_hints,
            state_store=state_store,
            # Wire up telemetry callback
            event_callback=self.telemetry.record_planner_event,
        )

        self.telemetry.logger.info(
            "planner_configured_v2",
            extra={
                "model": self.config.llm_model,
                "max_iters": self.config.planner_max_iters,
                "token_budget": self.config.planner_token_budget,
                "reflection_enabled": self.config.reflection_enabled,
                "tool_policy_enabled": self.config.tool_policy_enabled,
                "planning_hints_enabled": self.config.planning_hints_enabled,
                "state_store_enabled": self.config.state_store_enabled,
            },
        )

        return planner

    async def execute(
        self,
        query: str,
        *,
        tenant_id: str = "default",
        memories: list[dict[str, Any]] | None = None,
    ) -> FinalAnswer:
        """Execute agent planning for a user query.

        Args:
            query: The user's question or request
            tenant_id: Tenant identifier for multi-tenancy
            memories: Optional conversation history and context memories.
                     Each memory can be a dict with fields like:
                     - role: "user" | "assistant" | "system"
                     - content: str
                     - timestamp: str
                     - metadata: dict

        Example:
            >>> memories = [
            ...     {
            ...         "role": "user",
            ...         "content": "Deploy version 2.3.1",
            ...         "timestamp": "2025-10-20",
            ...     },
            ...     {"role": "assistant", "content": "Deployed successfully to prod"},
            ...     {"role": "system", "content": "User prefers verbose explanations"},
            ... ]
            >>> result = await agent.execute("What was deployed?", memories=memories)
        """
        trace_id = uuid4().hex
        status_history: list[StatusUpdate] = STATUS_BUFFER[trace_id]
        status_history_for_llm: list[dict[str, Any]] = []

        def publish_status(update: StatusUpdate) -> None:
            status_history.append(update)
            status_history_for_llm.append(update.model_dump())
            message_text = update.message or ""
            step_ref = str(update.roadmap_step_id) if update.roadmap_step_id is not None else "-"
            EXECUTION_LOGS.append(f"{trace_id}:{update.status}:{message_text}:{step_ref}")
            self.telemetry.logger.debug(
                "status_update_buffered",
                extra={
                    "trace_id": trace_id,
                    "status": update.status,
                    "message": update.message,
                    "step_id": update.roadmap_step_id,
                    "step_status": update.roadmap_step_status,
                },
            )

        # CRYSTAL CLEAR PATTERN: Split context into two explicit dicts
        # This demonstrates the recommended pattern for PenguiFlow developers
        #
        # llm_context: Data visible to LLM (sent in user message)
        #   - Only include information useful for planning decisions
        #   - NO internal routing metadata (tenant_id, trace_id)
        #   - Format can be customized per application
        #
        # tool_context: Data visible ONLY to nodes (via ctx.tool_context)
        #   - Internal concerns: routing, logging, telemetry
        #   - Non-serializable objects: functions, loggers
        #   - Never sent to LLM

        llm_context: dict[str, Any] = {
            "status_history": status_history_for_llm,  # Can help LLM track progress
        }

        # Add memories if provided - these ARE sent to the LLM!
        if memories:
            llm_context["memories"] = memories

        # Node metadata - internal concerns only
        tool_context: dict[str, Any] = {
            "tenant_id": tenant_id,
            "trace_id": trace_id,
            "status_publisher": publish_status,
            "telemetry": self.telemetry,
            "status_logger": self.telemetry.logger,
        }

        self.telemetry.logger.info(
            "execute_start",
            extra={"query": query, "tenant_id": tenant_id, "trace_id": trace_id},
        )

        finish: FinalAnswer | None = None

        try:
            planner_result = await self._planner.run(
                query=query,
                llm_context=llm_context,
                tool_context=tool_context,
            )

            if isinstance(planner_result, PlannerPause):
                publish_status(
                    StatusUpdate(
                        status="thinking",
                        message="Planner paused awaiting external input",
                    )
                )
                finish = FinalAnswer(
                    text="Workflow paused awaiting external input.",
                    route="pause",
                    metadata={
                        "reason": planner_result.reason,
                        "payload": dict(planner_result.payload),
                        "resume_token": planner_result.resume_token,
                    },
                )
            elif planner_result.reason == "answer_complete":
                final_answer = FinalAnswer.model_validate(planner_result.payload)
                metadata = dict(final_answer.metadata)
                planner_meta = dict(planner_result.metadata)
                metadata.setdefault("trace_id", trace_id)
                if planner_meta:
                    metadata.setdefault("planner", planner_meta)
                final_answer = final_answer.model_copy(update={"metadata": metadata})
                self.telemetry.logger.info(
                    "execute_success",
                    extra={
                        "route": final_answer.route,
                        "trace_id": trace_id,
                        "step_count": planner_meta.get("step_count", 0),
                    },
                )
                finish = final_answer
            elif planner_result.reason == "no_path":
                publish_status(
                    StatusUpdate(
                        status="error",
                        message="Planner could not find a viable path",
                    )
                )
                planner_meta = dict(planner_result.metadata)
                meta = {"error": "no_path", "planner": planner_meta}
                finish = FinalAnswer(
                    text=(f"I couldn't complete the task. Reason: {planner_meta.get('thought', 'Unknown')}"),
                    route="error",
                    metadata=meta,
                )
                self.telemetry.logger.warning(
                    "execute_no_path",
                    extra={
                        "trace_id": trace_id,
                        "reason": planner_meta.get("thought"),
                        "step_count": planner_meta.get("step_count", 0),
                    },
                )
            elif planner_result.reason == "budget_exhausted":
                publish_status(
                    StatusUpdate(
                        status="error",
                        message="Budget exhausted before completion",
                    )
                )
                planner_meta = dict(planner_result.metadata)
                meta = {"error": "budget_exhausted", "planner": planner_meta}
                finish = FinalAnswer(
                    text=("Task interrupted due to resource constraints. Partial results may be available."),
                    route="error",
                    metadata=meta,
                )
                self.telemetry.logger.warning(
                    "execute_budget_exhausted",
                    extra={
                        "trace_id": trace_id,
                        "constraints": planner_meta.get("constraints", {}),
                        "step_count": planner_meta.get("step_count", 0),
                    },
                )
            else:
                raise RuntimeError(f"Unexpected planner result: {planner_result.reason}")

            assert finish is not None
            metadata = dict(finish.metadata)
            metadata.setdefault("trace_id", trace_id)
            finish = finish.model_copy(update={"metadata": metadata})
            return finish

        except Exception as exc:
            self.telemetry.logger.exception(
                "execute_error",
                extra={
                    "query": query,
                    "tenant_id": tenant_id,
                    "trace_id": trace_id,
                    "error_class": exc.__class__.__name__,
                    "error_message": str(exc),
                },
            )
            raise
        finally:
            if self.config.enable_telemetry:
                self.telemetry.emit_collected_events()

    def get_metrics(self) -> dict:
        """Return current telemetry metrics."""
        return dict(self.telemetry.get_metrics())

    def reset_metrics(self) -> None:
        """Reset telemetry counters (for testing)."""
        self.telemetry.reset_metrics()


def _format_status_for_terminal(update: StatusUpdate, trace_id: str) -> str:
    """Format status update for terminal display (simulating WebSocket/SSE)."""
    status_icon = {
        "thinking": "🤔",
        "ok": "✅",
        "error": "❌",
    }.get(update.status, "ℹ️")

    step_status_icon = {
        "running": "▶️ ",
        "ok": "✓ ",
        "error": "✗ ",
    }.get(update.roadmap_step_status or "", "")

    parts = [f"{status_icon} [{update.status.upper()}]"]

    if update.roadmap_step_id is not None:
        parts.append(f"[Step {update.roadmap_step_id}]")

    if update.roadmap_step_status:
        parts.append(f"{step_status_icon}{update.roadmap_step_status}")

    if update.message:
        parts.append(f"{update.message}")

    if update.roadmap_step_id is None and update.roadmap_step_list:
        parts.append(f"Roadmap: {len(update.roadmap_step_list)} steps")

    return " ".join(parts)


async def _monitor_and_stream_status(stream_enabled: bool = False) -> str | None:
    """Monitor STATUS_BUFFER for new trace_ids and stream updates in real-time."""
    if not stream_enabled:
        return None

    seen_traces = set(STATUS_BUFFER.keys())
    trace_counts: dict[str, int] = {tid: len(updates) for tid, updates in STATUS_BUFFER.items()}

    while True:
        # Check for new trace IDs
        current_traces = set(STATUS_BUFFER.keys())
        new_traces = current_traces - seen_traces

        if new_traces:
            # Found a new trace - this is the one we're tracking
            trace_id = list(new_traces)[0]
            seen_traces.add(trace_id)
            trace_counts[trace_id] = 0

        # Stream updates for all active traces
        for trace_id in list(current_traces):
            updates = STATUS_BUFFER.get(trace_id, [])
            last_count = trace_counts.get(trace_id, 0)

            if len(updates) > last_count:
                for update in updates[last_count:]:
                    formatted = _format_status_for_terminal(update, trace_id)
                    print(f"  │ {formatted}", file=sys.stderr, flush=True)
                trace_counts[trace_id] = len(updates)

        await asyncio.sleep(0.05)  # Check every 50ms


async def main() -> None:
    """Example usage of enterprise agent."""
    # Parse CLI arguments
    parser = argparse.ArgumentParser(
        description="Enterprise Agent with ReactPlanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Run without streaming
  python main.py --stream          # Show real-time status updates
  python main.py --query "Analyze logs"  # Custom query
        """,
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Display real-time status updates (simulates WebSocket/SSE feed)",
    )
    parser.add_argument(
        "--query",
        type=str,
        help="Run a single custom query instead of examples",
    )
    args = parser.parse_args()

    # Load environment variables from .env file
    from pathlib import Path

    from dotenv import load_dotenv

    # Try loading from example directory first, then project root
    example_dir = Path(__file__).parent
    project_root = example_dir.parent.parent
    env_path = example_dir / ".env"
    if not env_path.exists():
        env_path = project_root / ".env"
    load_dotenv(env_path)

    # Load configuration from environment
    config = AgentConfig.from_env()

    # Create orchestrator
    agent = EnterpriseAgentOrchestrator(config)

    # Determine which queries to run
    if args.query:
        queries = [args.query]
    else:
        queries = [
            "Analyze the latest deployment logs and summarize findings",
            "We're seeing a ValueError in production, help diagnose",
            "What's the status of the API service?",
        ]

    # Example: Passing memories for context-aware planning
    # In production, these would come from a conversation database
    example_memories = [
        {
            "role": "user",
            "content": "Deploy version 2.3.1 to production",
            "timestamp": "2025-10-20T14:30:00Z",
        },
        {
            "role": "assistant",
            "content": "Deployed v2.3.1 to production successfully",
            "timestamp": "2025-10-20T14:35:00Z",
        },
        {
            "role": "system",
            "content": "User prefers detailed explanations with code snippets",
            "metadata": {"user_preference": "verbose"},
        },
    ]

    # Start global streaming monitor if enabled
    stream_task = None
    if args.stream:
        stream_task = asyncio.create_task(_monitor_and_stream_status(args.stream))

    for i, query in enumerate(queries, 1):
        print(f"\n{'=' * 80}")
        print(f"Query {i}: {query}")
        print("=" * 80)

        if args.stream:
            print("\n  ┌─ Real-time Status Stream ─────────────")

        try:
            # Pass memories on the first query to demonstrate context awareness
            memories = example_memories if i == 1 and not args.query else None
            if memories:
                print(f"\n[Using {len(memories)} memories for context]")

            # Execute query
            result = await agent.execute(query, memories=memories)

            # Wait a bit for any final status updates
            if stream_task and not stream_task.done():
                await asyncio.sleep(0.2)

            if args.stream:
                print("  └───────────────────────────────────────\n")

            print(f"\nRoute: {result.route}")
            print(f"Answer: {result.text}")

            if result.artifacts:
                print("\nArtifacts:")
                for key, value in result.artifacts.items():
                    if isinstance(value, list) and len(value) > 3:
                        print(f"  {key}: [{len(value)} items]")
                    else:
                        print(f"  {key}: {value}")

            if result.metadata:
                print("\nMetadata:")
                for key, value in result.metadata.items():
                    print(f"  {key}: {value}")

        except Exception as exc:
            if args.stream:
                print("  └───────────────────────────────────────\n")
            print(f"\nError: {exc.__class__.__name__}: {exc}")

    # Clean up global streaming task
    if stream_task and not stream_task.done():
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass

    # Show metrics
    print(f"\n{'=' * 80}")
    print("Telemetry Metrics")
    print("=" * 80)
    metrics = agent.get_metrics()
    for key, value in metrics.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
