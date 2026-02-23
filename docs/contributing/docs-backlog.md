# Docs backlog (source of truth)

This backlog is the single inventory for **all** markdown documentation under `docs/`.

It exists to make the “enterprise depth” work measurable:

- every file has a Tier (A/B/C), an owner area, and a target phase
- Tier A is curated and must meet the rubric in `docs-style.md`
- everything else stays out of the curated site unless promoted

## Legend

- **Tier**
  - **A**: curated (in `mkdocs.yml` nav) — enterprise depth required
  - **B**: canonical internal guide / extraction source — must remain accurate
  - **C**: draft/internal notes — useful but not blocking
  - **shim**: compatibility redirect/forwarder — should be referenced only for legacy links
- **Area**: subsystem owner (planner/tools/core/ops/etc.)
- **Outcome** (what we do with the file): `keep | split | merge | deprecate`
- **Status** (how it’s used): `canonical | extract | draft | shim`
- **Target phase**: 0–6 (from the phased plan)

!!! note
    Tier A is defined mechanically: it is the set of markdown files present in `mkdocs.yml` `nav:`.
    Any file not in nav is not “enterprise bar blocking” unless explicitly promoted.

## Inventory (all `docs/**/*.md`)

Counts (current):

- Tier A: 55
- Tier B: 12
- Tier C: 60
- Shims: 5
- Total: 132

| File | Tier | Area | Outcome | Status | Target phase |
|---|:---:|---|---|---|---:|
| `docs/A2A_COMPLIANCE_GAP_ANALYSIS.md` | C | misc | keep | draft | 6 |
| `docs/LLM_LOGGING.md` | B | misc | split | extract | 4 |
| `docs/MEMORY_GUIDE.md` | shim | misc | deprecate | shim | 1 |
| `docs/MIGRATION_V24.md` | shim | misc | deprecate | shim | 6 |
| `docs/PLAYGROUND_BACKEND_CONTRACTS.md` | B | misc | split | extract | 4 |
| `docs/PROPOSED_PLANNER_PROMPT.md` | C | misc | keep | draft | 6 |
| `docs/RFC/Done/PLAN_CHAT_STEERING_UX.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_AGENT_BACKGROUND_TASKS.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_AGUI_COMPONENTS.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_AGUI_INTEGRATION.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_AUTO_EXECUTION.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_BIDIRECTIONAL_PROTOCOL.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_LLM_ERROR_RECOVERY.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_MCP_BINARY_CONTENT_HANDLING.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_NATIVE_LLM_LAYER.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_REACT_REFACTOR_OUTPUT_STRATEGY.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_STRUCTURED_PLANNER_OUTPUT.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_TASK_GROUPS.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_TOOL_AND_SKILL_SEARCH.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_UNIFIED_ACTION_SCHEMA.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/Done/RFC_UNIFIED_STATESTORE.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/ToDo/RFC_IDEAS_BACKLOG_2026_01.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/ToDo/RFC_REACTPLANNER_VISION_INPUT.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/ToDo/RFC_STATESTORE_STANDARD_FOLLOWUPS.md` | C | RFC | keep | draft | 6 |
| `docs/RFC/ToDo/RICH_OUTPUT_WRAPPER_TOOLS.md` | C | RFC | keep | draft | 6 |
| `docs/RFC_SCHEDULED_TASKS.md` | C | misc | keep | draft | 6 |
| `docs/agui/README.md` | C | agui | keep | draft | 6 |
| `docs/agui/flow-artifacts-resources.md` | C | agui | keep | draft | 6 |
| `docs/agui/flow-context-mapping.md` | C | agui | keep | draft | 6 |
| `docs/agui/flow-end-to-end.md` | C | agui | keep | draft | 6 |
| `docs/agui/flow-event-mapping.md` | C | agui | keep | draft | 6 |
| `docs/agui/flow-pause.md` | C | agui | keep | draft | 6 |
| `docs/agui/flow-tool-calls.md` | C | agui | keep | draft | 6 |
| `docs/api/short-term-memory.md` | shim | api | deprecate | shim | 1 |
| `docs/architecture/core_runtime/runtime_infrastructure.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/infrastructure/distributed_execution.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/infrastructure/observability_monitoring.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/infrastructure/performance_scalability.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/infrastructure/security_compliance.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/infrastructure/state_management_persistence.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/infrastructure/streaming_realtime.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/integration_connectivity/llm_interface.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/integration_connectivity/native_llm_policy_table.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/integration_connectivity/tool_execution.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/planning_orchestration/MEMORY_GUIDE.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/planning_orchestration/guardrails_policy_packs.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/planning_orchestration/memory_management.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/planning_orchestration/reactplanner_core.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/session_management/background_tasks_steering_system.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/session_management/session_management.md` | C | architecture | keep | draft | 6 |
| `docs/architecture/system_architecture_overview.md` | B | architecture | keep | extract | 6 |
| `docs/cli/dev-command.md` | A | cli | keep | canonical | 5 |
| `docs/cli/generate-command.md` | A | cli | keep | canonical | 5 |
| `docs/cli/init-command.md` | A | cli | keep | canonical | 5 |
| `docs/cli/new-command.md` | A | cli | keep | canonical | 5 |
| `docs/cli/overview.md` | A | cli | keep | canonical | 5 |
| `docs/cli/tools-command.md` | A | cli | keep | canonical | 5 |
| `docs/contributing/dev-setup.md` | A | contributing | keep | canonical | 5 |
| `docs/contributing/docs-backlog.md` | A | contributing | keep | canonical | 0 |
| `docs/contributing/docs-style.md` | A | contributing | keep | canonical | 0 |
| `docs/contributing/docs-uplift-plan.md` | A | contributing | keep | canonical | 0 |
| `docs/contributing/releasing.md` | A | contributing | keep | canonical | 5 |
| `docs/contributing/testing.md` | A | contributing | keep | canonical | 5 |
| `docs/core/cancellation.md` | A | core | keep | canonical | 3 |
| `docs/core/concurrency.md` | A | core | keep | canonical | 3 |
| `docs/core/errors-retries-timeouts.md` | A | core | keep | canonical | 3 |
| `docs/core/flows-and-nodes.md` | A | core | keep | canonical | 3 |
| `docs/core/messages-and-envelopes.md` | A | core | keep | canonical | 3 |
| `docs/core/playbooks.md` | A | core | keep | canonical | 6 |
| `docs/core/routers-and-policies.md` | A | core | keep | canonical | 6 |
| `docs/core/streaming.md` | A | core | keep | canonical | 3 |
| `docs/core_behavior_spec.md` | B | misc | split | extract | 3 |
| `docs/deployment/overview.md` | A | deployment | keep | canonical | 4 |
| `docs/deployment/distributed-execution.md` | A | deployment | keep | canonical | 6 |
| `docs/deployment/production-deployment.md` | A | deployment | keep | canonical | 4 |
| `docs/deployment/worker-integration.md` | A | deployment | keep | canonical | 4 |
| `docs/getting-started/concepts.md` | A | getting-started | keep | canonical | 3 |
| `docs/getting-started/installation.md` | A | getting-started | keep | canonical | 3 |
| `docs/getting-started/quickstart.md` | A | getting-started | keep | canonical | 3 |
| `docs/guardrails/INTEGRATIONS.md` | C | guardrails | keep | draft | 6 |
| `docs/howto/uv-spec-workflow.md` | C | howto | keep | draft | 6 |
| `docs/implementation/RFC_NATIVE_LLM_LAYER/STATUS.md` | C | implementation | keep | draft | 6 |
| `docs/index.md` | A | misc | keep | canonical | 0 |
| `docs/investigations/BACKGROUND_TASKS_FOLLOWUPS.md` | C | investigations | keep | draft | 6 |
| `docs/migration/MEMORY_ADOPTION.md` | B | migration | keep | extract | 1 |
| `docs/migration/penguiflow-adoption.md` | B | migration | keep | extract | 6 |
| `docs/migration/upgrade-notes.md` | B | migration | keep | extract | 6 |
| `docs/observability/logging.md` | A | observability | keep | canonical | 4 |
| `docs/observability/metrics-and-alerts.md` | A | observability | keep | canonical | 4 |
| `docs/observability/telemetry-patterns.md` | A | observability | keep | canonical | 4 |
| `docs/patterns/ROUTER_PLAYBOOK_GUIDE.md` | C | patterns | keep | draft | 6 |
| `docs/patterns/TOOL_VISIBILITY_GUIDE.md` | C | patterns | keep | draft | 6 |
| `docs/patterns/roadmap_status_updates.md` | C | patterns | keep | draft | 6 |
| `docs/patterns/topic-generation-flow.md` | C | patterns | keep | draft | 6 |
| `docs/planner/actions-and-schema.md` | A | planner | keep | canonical | 1 |
| `docs/planner/background-tasks.md` | A | planner | keep | canonical | 1 |
| `docs/planner/configuration.md` | A | planner | keep | canonical | 1 |
| `docs/planner/guardrails.md` | A | planner | keep | canonical | 1 |
| `docs/planner/llm-clients.md` | A | planner | keep | canonical | 1 |
| `docs/planner/memory.md` | A | planner | keep | canonical | 1 |
| `docs/planner/native-llm.md` | A | planner | keep | canonical | 1 |
| `docs/planner/observability.md` | A | planner | keep | canonical | 1 |
| `docs/planner/overview.md` | A | planner | keep | canonical | 1 |
| `docs/planner/parallel-and-joins.md` | A | planner | keep | canonical | 1 |
| `docs/planner/pause-resume-hitl.md` | A | planner | keep | canonical | 1 |
| `docs/planner/steering.md` | A | planner | keep | canonical | 1 |
| `docs/planner/tool-design.md` | A | planner | keep | canonical | 1 |
| `docs/planner/tooling.md` | A | planner | keep | canonical | 2 |
| `docs/planner/troubleshooting.md` | A | planner | keep | canonical | 1 |
| `docs/production-deployment.md` | shim | misc | deprecate | shim | 4 |
| `docs/proposals/RFC_SHORT_TERM_MEMORY.md` | C | proposals | keep | draft | 6 |
| `docs/proposals/RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md` | C | proposals | keep | draft | 6 |
| `docs/reference/public-api.md` | A | reference | keep | canonical | 6 |
| `docs/reference/short-term-memory-api.md` | A | reference | keep | canonical | 1 |
| `docs/reference/testing.md` | A | reference | keep | canonical | 6 |
| `docs/reference/visualization.md` | A | reference | keep | canonical | 6 |
| `docs/spec/README.md` | C | spec | keep | draft | 6 |
| `docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md` | C | spec | keep | draft | 6 |
| `docs/spec/a2a_specification.md` | C | spec | keep | draft | 6 |
| `docs/telemetry-patterns.md` | shim | misc | deprecate | shim | 4 |
| `docs/tools/a2a-agent-tools.md` | C | tools | keep | draft | 6 |
| `docs/tools/artifacts-and-resources.md` | A | tools | keep | canonical | 2 |
| `docs/tools/artifacts-guide.md` | B | tools | split | extract | 2 |
| `docs/tools/concurrency-guide.md` | B | tools | split | extract | 2 |
| `docs/tools/configuration-guide.md` | B | tools | split | extract | 2 |
| `docs/tools/configuration.md` | A | tools | keep | canonical | 2 |
| `docs/tools/mcp-resources.md` | A | tools | keep | canonical | 2 |
| `docs/tools/mcp-resources-guide.md` | B | tools | split | extract | 2 |
| `docs/tools/oauth-hitl.md` | A | tools | keep | canonical | 2 |
| `docs/tools/statestore-guide.md` | B | tools | split | extract | 2 |
| `docs/tools/statestore.md` | A | tools | keep | canonical | 2 |

## Notes / follow-ups

- `docs/*_GUIDE.md` shims exist to preserve old links; do not add new references to them.
- Large internal docs marked `split` are expected to be decomposed into Tier A pages (or rewritten as Tier B indices) during their target phases.
