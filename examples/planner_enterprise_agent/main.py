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

import asyncio
import logging
from collections import defaultdict

from penguiflow import log_flow_events
from penguiflow.catalog import build_catalog
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.registry import ModelRegistry

from .config import AgentConfig
from .nodes import (
    FinalAnswer,
    StatusUpdate,
    UserQuery,
    answer_general_query,
    chunk_sink_node,
    collect_error_logs,
    extract_metadata,
    generate_document_summary,
    initialize_bug_workflow,
    initialize_document_workflow,
    parse_documents,
    recommend_bug_fix,
    render_document_report,
    run_diagnostics,
    status_sink_node,
    triage_query,
)
from .telemetry import AgentTelemetry


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
        """Construct all planner-discoverable nodes."""
        return [
            # Router
            Node(triage_query, name="triage_query"),
            # Document workflow
            Node(initialize_document_workflow, name="init_documents"),
            Node(parse_documents, name="parse_documents"),
            Node(extract_metadata, name="extract_metadata"),
            Node(generate_document_summary, name="generate_summary"),
            Node(render_document_report, name="render_report"),
            # Bug workflow
            Node(initialize_bug_workflow, name="init_bug"),
            Node(collect_error_logs, name="collect_logs"),
            Node(run_diagnostics, name="run_diagnostics"),
            Node(recommend_bug_fix, name="recommend_fix"),
            # General
            Node(answer_general_query, name="answer_general"),
        ]

    def _build_registry(self) -> ModelRegistry:
        """Register all type mappings for validation."""
        registry = ModelRegistry()

        # Import types
        from .nodes import (
            BugState,
            DocumentState,
            GeneralResponse,
            RouteDecision,
        )

        # Router
        registry.register("triage_query", UserQuery, RouteDecision)

        # Document workflow
        registry.register("init_documents", RouteDecision, DocumentState)
        registry.register("parse_documents", DocumentState, DocumentState)
        registry.register("extract_metadata", DocumentState, DocumentState)
        registry.register("generate_summary", DocumentState, DocumentState)
        registry.register("render_report", DocumentState, FinalAnswer)

        # Bug workflow
        registry.register("init_bug", RouteDecision, BugState)
        registry.register("collect_logs", BugState, BugState)
        registry.register("run_diagnostics", BugState, BugState)
        registry.register("recommend_fix", BugState, FinalAnswer)

        # General
        registry.register("answer_general", RouteDecision, FinalAnswer)

        return registry

    def _build_planner(self) -> ReactPlanner:
        """Construct ReactPlanner with enterprise configuration."""
        catalog = build_catalog(self._nodes, self._registry)

        # CRITICAL: Set event_callback for planner observability
        planner = ReactPlanner(
            llm=self.config.llm_model,
            catalog=catalog,
            max_iters=self.config.planner_max_iters,
            temperature=self.config.llm_temperature,
            json_schema_mode=True,
            token_budget=self.config.planner_token_budget,
            deadline_s=self.config.planner_deadline_s,
            hop_budget=self.config.planner_hop_budget,
            summarizer_llm=self.config.summarizer_model,
            llm_timeout_s=self.config.llm_timeout_s,
            llm_max_retries=self.config.llm_max_retries,
            absolute_max_parallel=self.config.planner_absolute_max_parallel,
            # Wire up telemetry callback
            event_callback=self.telemetry.record_planner_event,
        )

        self.telemetry.logger.info(
            "planner_configured",
            extra={
                "model": self.config.llm_model,
                "max_iters": self.config.planner_max_iters,
                "token_budget": self.config.planner_token_budget,
            },
        )

        return planner

    async def execute(self, query: str, *, tenant_id: str = "default") -> FinalAnswer:
        """Execute agent planning for a user query.

        This is the main entry point for agent execution. It:
        1. Creates a UserQuery from input
        2. Runs the ReactPlanner to autonomously select and sequence nodes
        3. Handles errors gracefully with detailed logging
        4. Emits telemetry to observability backends
        5. Returns structured final answer

        Parameters:
            query: Natural language user query
            tenant_id: Multi-tenant identifier for isolation

        Returns:
            FinalAnswer with results and metadata

        Raises:
            RuntimeError: If planner fails after retries
            ValueError: If query is invalid
        """
        user_query = UserQuery(text=query, tenant_id=tenant_id)

        self.telemetry.logger.info(
            "execute_start",
            extra={"query": query, "tenant_id": tenant_id},
        )

        try:
            # Run planner (LLM will autonomously select and sequence nodes)
            result = await self._planner.run(
                query=query,
                context_meta={"tenant_id": tenant_id, "query": query},
            )

            # Check result type
            if result.reason == "answer_complete":
                final_answer = FinalAnswer.model_validate(result.payload)

                self.telemetry.logger.info(
                    "execute_success",
                    extra={
                        "route": final_answer.route,
                        "step_count": result.metadata.get("step_count", 0),
                    },
                )

                # Emit collected telemetry
                if self.config.enable_telemetry:
                    self.telemetry.emit_collected_events()

                return final_answer

            elif result.reason == "no_path":
                # Planner couldn't find a solution
                self.telemetry.logger.warning(
                    "execute_no_path",
                    extra={
                        "reason": result.metadata.get("thought"),
                        "step_count": result.metadata.get("step_count", 0),
                    },
                )

                # Return fallback answer
                return FinalAnswer(
                    text=(
                        f"I couldn't complete the task. "
                        f"Reason: {result.metadata.get('thought', 'Unknown')}"
                    ),
                    route="error",
                    metadata={"error": "no_path", **result.metadata},
                )

            elif result.reason == "budget_exhausted":
                # Hit constraints (deadline, hop budget, etc.)
                self.telemetry.logger.warning(
                    "execute_budget_exhausted",
                    extra={
                        "constraints": result.metadata.get("constraints", {}),
                        "step_count": result.metadata.get("step_count", 0),
                    },
                )

                return FinalAnswer(
                    text=(
                        f"Task interrupted due to resource constraints. "
                        f"Partial results may be available."
                    ),
                    route="error",
                    metadata={"error": "budget_exhausted", **result.metadata},
                )

            else:
                # Unexpected result type
                raise RuntimeError(f"Unexpected planner result: {result.reason}")

        except Exception as exc:
            # Comprehensive error logging
            self.telemetry.logger.exception(
                "execute_error",
                extra={
                    "query": query,
                    "tenant_id": tenant_id,
                    "error_class": exc.__class__.__name__,
                    "error_message": str(exc),
                },
            )

            # Re-raise for caller to handle
            raise

    def get_metrics(self) -> dict:
        """Return current telemetry metrics."""
        return dict(self.telemetry.get_metrics())

    def reset_metrics(self) -> None:
        """Reset telemetry counters (for testing)."""
        self.telemetry.reset_metrics()


async def main() -> None:
    """Example usage of enterprise agent."""
    # Load configuration from environment
    config = AgentConfig.from_env()

    # Create orchestrator
    agent = EnterpriseAgentOrchestrator(config)

    # Run example queries
    queries = [
        "Analyze the latest deployment logs and summarize findings",
        "We're seeing a ValueError in production, help diagnose",
        "What's the status of the API service?",
    ]

    for i, query in enumerate(queries, 1):
        print(f"\n{'=' * 80}")
        print(f"Query {i}: {query}")
        print('=' * 80)

        try:
            result = await agent.execute(query)

            print(f"\nRoute: {result.route}")
            print(f"Answer: {result.text}")

            if result.artifacts:
                print(f"\nArtifacts:")
                for key, value in result.artifacts.items():
                    if isinstance(value, list) and len(value) > 3:
                        print(f"  {key}: [{len(value)} items]")
                    else:
                        print(f"  {key}: {value}")

            if result.metadata:
                print(f"\nMetadata:")
                for key, value in result.metadata.items():
                    print(f"  {key}: {value}")

        except Exception as exc:
            print(f"\nError: {exc.__class__.__name__}: {exc}")

    # Show metrics
    print(f"\n{'=' * 80}")
    print("Telemetry Metrics")
    print('=' * 80)
    metrics = agent.get_metrics()
    for key, value in metrics.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
