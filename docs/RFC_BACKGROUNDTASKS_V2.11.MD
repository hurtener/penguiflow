Background Tasks v2.11 - Design Document

  Decisions Made

  | Decision              | Choice                    | Rationale                                       |
  |-----------------------|---------------------------|------------------------------------------------|
  | Proactive generator   | Full planner loop         | Agent needs full toolbox access                |
  | Group task reporting  | Combined single message   | Grouped = used together                        |
  | Cleanup timing        | After agent uses results  | APPEND is default; must be used before cleanup |
  | AG-UI first-class     | Deferred                  | /session/stream sufficient for now             |
  | Storage location      | Trajectory (new field)    | Reuses compression, ephemeral by design        |
  | Proactive default     | Explicit opt-in required  | Backward compatibility with tests              |
  | LLM context handling  | Extract + remove pattern  | Prevents double-rendering, consistent design   |
  | Proactive recursion   | Max hops = 2 (configurable) | Safety net against recursive background loops |
  | Context snapshot      | Short-term memory summary | Avoids heavy llm_context duplication           |

  ---
  Architecture Overview

  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                     BACKGROUND TASK COMPLETION FLOW                          │
  ├─────────────────────────────────────────────────────────────────────────────┤
  │                                                                              │
  │  Background Task Completes                                                   │
  │      ↓                                                                       │
  │  Build BackgroundTaskResult                                                  │
  │      ↓                                                                       │
  │  Store in trajectory.background_results[task_id]  ◄─── NEW: Trajectory field │
  │      ↓                                                                       │
  │  Queue ProactiveReportRequest (with short-term memory snapshot)              │
  │      ↓                                                                       │
  │  ┌────────────────────────────────────────────┐                             │
  │  │ Proactive Reporter (background task)       │                             │
  │  │   • Wait for foreground idle               │                             │
  │  │   • Call default_proactive_generator()     │                             │
  │  └────────────────────────────────────────────┘                             │
  │      ↓                                                                       │
  │  ┌────────────────────────────────────────────┐                             │
  │  │ Default Proactive Generator                │                             │
  │  │   • Run FULL planner loop (with toolbox)   │  ◄─── NEW: Library default  │
  │  │   • Proactive hop guard (default max=2)    │                             │
  │  │   • Main agent synthesizes natural message │                             │
  │  │   • Has short-term memory context          │                             │
  │  │   • Publishes as StateUpdate.RESULT        │                             │
  │  └────────────────────────────────────────────┘                             │
  │      ↓                                                                       │
  │  Message delivered to frontend via UpdateBroker                              │
  │      ↓                                                                       │
  │  Agent marks result as consumed                                              │
  │      ↓                                                                       │
  │  trajectory.background_results[task_id] cleaned up  ◄─── NEW: Cleanup hook  │
  │                                                                              │
  └─────────────────────────────────────────────────────────────────────────────┘

  ---
  Component 1: Trajectory Extension

  File: penguiflow/planner/trajectory.py

  @dataclass(slots=True)
  class BackgroundTaskResult:
      """Result from a completed background task, stored in trajectory."""
      task_id: str
      group_id: str | None = None
      status: Literal["completed", "failed"] = "completed"
      summary: str | None = None          # Short text for LLM (~100 tokens)
      payload: Any = None                  # Full result (not sent to LLM by default)
      facts: dict[str, Any] = field(default_factory=dict)
      artifacts: list[dict[str, Any]] = field(default_factory=list)
      consumed: bool = False               # Marked True after agent uses it
      completed_at: float = field(default_factory=time.time)


  @dataclass(slots=True)
  class Trajectory:
      query: str
      llm_context: Mapping[str, Any] | None = None
      tool_context: dict[str, Any] | None = None
      # ... existing fields ...

      # NEW: Background results (ephemeral, compressed, consumption-tracked)
      background_results: dict[str, BackgroundTaskResult] = field(default_factory=dict)

      def add_background_result(self, result: BackgroundTaskResult) -> None:
          """Add a background task result for the agent to process."""
          self.background_results[result.task_id] = result

      def mark_background_consumed(self, task_id: str) -> bool:
          """Mark a background result as consumed by the agent."""
          if task_id in self.background_results:
              self.background_results[task_id].consumed = True
              return True
          return False

      def clear_consumed_background(self) -> int:
          """Remove all consumed background results. Returns count removed."""
          to_remove = [tid for tid, r in self.background_results.items() if r.consumed]
          for tid in to_remove:
              del self.background_results[tid]
          return len(to_remove)

      def get_unconsumed_background(self) -> dict[str, BackgroundTaskResult]:
          """Get background results that haven't been consumed yet."""
          return {tid: r for tid, r in self.background_results.items() if not r.consumed}

  Serialization updates:

  def serialise(self) -> dict[str, Any]:
      return {
          # ... existing ...
          "background_results": {
              task_id: {
                  "task_id": r.task_id,
                  "group_id": r.group_id,
                  "status": r.status,
                  "summary": r.summary,
                  "payload": r.payload,
                  "facts": r.facts,
                  "artifacts": r.artifacts,
                  "consumed": r.consumed,
                  "completed_at": r.completed_at,
              }
              for task_id, r in self.background_results.items()
          },
      }

  @classmethod
  def from_serialised(cls, payload: Mapping[str, Any]) -> Trajectory:
      # ... existing ...
      for task_id, data in payload.get("background_results", {}).items():
          trajectory.background_results[task_id] = BackgroundTaskResult(**data)
      return trajectory

  ---
  Component 2: Extended ProactiveReportRequest

  File: penguiflow/sessions/models.py

  class ProactiveReportRequest(BaseModel):
      """Request for proactive message generation with short-term memory context."""
      task_id: str
      session_id: str
      trace_id: str | None = None
      task_description: str | None = None
      execution_time_ms: int | None = None
      patch: ContextPatch
      merge_strategy: MergeStrategy
      queued_at: datetime = Field(default_factory=_utc_now)
      message_id: str = Field(default_factory=lambda: f"proactive_{secrets.token_hex(6)}")
      group_id: str | None = None

      # NEW: Short-term memory snapshot (condensed summary + recent turns)
      memory_summary: dict[str, Any] = Field(default_factory=dict)
      # Optional tool_context snapshot for tool execution continuity
      tool_context: dict[str, Any] = Field(default_factory=dict)
      # Reference to session context version/hash for debugging only
      context_version: int | None = None
      context_hash: str | None = None

      # NEW: For group tasks - combined results
      is_group_report: bool = False
      group_task_ids: list[str] = Field(default_factory=list)
      combined_patches: list[ContextPatch] = Field(default_factory=list)

  ---
  Component 3: Default Proactive Generator

  File: penguiflow/sessions/proactive.py (NEW)

  """Library-provided default proactive generator using full planner loop."""

  from penguiflow.planner import ReactPlanner
  from penguiflow.sessions.models import ProactiveReportRequest
  from penguiflow.sessions.session import StreamingSession
  from penguiflow.state.models import StateUpdate, UpdateType


  PROACTIVE_SYNTHESIS_PROMPT = """\
  You previously started a background investigation task.

  **Original task:** {task_description}

  **Results received:**
  {results_summary}

  Based on your short-term memory summary and these new results, write a natural, 
  conversational message to inform the user about what you found. 

  Guidelines:
  - Relate the findings to the ongoing discussion if relevant
  - Be conversational, not robotic or template-like
  - If the results are significant, explain their implications
  - If results relate to something discussed earlier, make that connection
  - Use your tools if you need to do additional work based on these results

  Remember: You have access to your full toolbox. If the results suggest further 
  investigation or action, you may proceed.
  """


  async def create_default_proactive_generator(
      planner_factory: Callable[[], ReactPlanner],
      session: StreamingSession,
  ) -> Callable[[ProactiveReportRequest], Awaitable[None]]:
      """
      Factory for the library default proactive generator.
      
      Uses full planner loop so agent has access to all tools.
      """

      async def generator(request: ProactiveReportRequest) -> None:
          # Build results summary
          if request.is_group_report:
              results_summary = _format_group_results(request)
          else:
              results_summary = _format_single_result(request)

          # Build the synthesis query
          query = PROACTIVE_SYNTHESIS_PROMPT.format(
              task_description=request.task_description or "Background task",
              results_summary=results_summary,
          )

          # Create planner instance
          planner = planner_factory()

          # Run full planner loop with short-term memory context
          result = await planner.run(
              query=query,
              llm_context={
                  "conversation_memory": request.memory_summary,
                  "_proactive_mode": True,
                  "_proactive_hops_remaining": 2,
                  "_background_task_id": request.task_id,
              },
              tool_context=request.tool_context,
          )

          # Extract the answer
          answer = _extract_answer(result.payload)

          # Publish as natural chat message
          session._publish(StateUpdate(
              session_id=request.session_id,
              task_id=request.task_id,
              trace_id=request.trace_id,
              update_type=UpdateType.RESULT,
              content={
                  "text": answer,
                  "channel": "answer",
                  "done": True,
                  "proactive": True,
                  "background_task_id": request.task_id,
                  "group_id": request.group_id,
              },
          ))

          # Mark results as consumed in trajectory
          # (cleanup happens after this via consumption tracking)
          await session.mark_background_consumed(
              task_ids=request.group_task_ids if request.is_group_report else [request.task_id]
          )

      return generator

  Proactive hop guard:
  - Each proactive run decrements `_proactive_hops_remaining`
  - When it reaches 0, disable background task spawning and proactive reporting
  - Default 2, configurable via template/spec generation


  def _format_single_result(request: ProactiveReportRequest) -> str:
      """Format a single task result for the synthesis prompt."""
      patch = request.patch
      parts = []

      if patch.digest:
          parts.append(f"Summary: {' '.join(patch.digest)}")

      if patch.facts:
          parts.append(f"Facts discovered: {json.dumps(patch.facts, indent=2)}")

      if patch.artifacts:
          parts.append(f"Artifacts produced: {len(patch.artifacts)} items")

      if patch.recommended_next_steps:
          parts.append(f"Suggested next steps: {', '.join(patch.recommended_next_steps)}")

      return "\n".join(parts) or "Task completed successfully."


  def _format_group_results(request: ProactiveReportRequest) -> str:
      """Format combined group task results for the synthesis prompt."""
      parts = [f"**{len(request.combined_patches)} related tasks completed:**\n"]

      for i, patch in enumerate(request.combined_patches, 1):
          task_summary = []
          if patch.digest:
              task_summary.append(' '.join(patch.digest))
          if patch.facts:
              task_summary.append(f"Facts: {json.dumps(patch.facts)}")

          parts.append(f"{i}. {' | '.join(task_summary) or 'Completed'}")

      return "\n".join(parts)

  ---
  Component 4: Session Integration

  File: penguiflow/sessions/session.py

  class StreamingSession:
      def __init__(self, ...):
          # ... existing ...

          # NOTE: Keep disabled by default for backward compatibility with tests.
          # Consumers (playground, orchestrators) must explicitly call
          # configure_proactive_reporting(enabled=True) to enable.
          self._proactive_config = {
              "enabled": False,  # ← UNCHANGED - explicit opt-in required
              "strategies": ["APPEND", "REPLACE"],
              "max_queued": 5,
              "timeout_s": 60.0,  # ← Increased for full planner loop
              "max_hops": 2,      # ← Proactive recursion safety net (configurable)
              "fallback_notification": True,
          }

      async def _enqueue_proactive_report(self, ...) -> None:
          """Queue proactive report with short-term memory snapshot."""
          # ... existing checks ...

          request = ProactiveReportRequest(
              task_id=task_id,
              session_id=self.session_id,
              trace_id=trace_id,
              task_description=description,
              execution_time_ms=execution_time_ms,
              patch=patch,
              merge_strategy=merge_strategy,
              # NEW: Include short-term memory snapshot (condensed)
              memory_summary=dict(self._context.memory),
              tool_context=dict(self._context.tool_context),
              context_version=self._context.version,
              context_hash=self._context.context_hash,
          )
          self._proactive_queue.put_nowait(request)

      async def mark_background_consumed(self, task_ids: list[str]) -> int:
          """Mark background results as consumed and clean up."""
          # This will be called after proactive message is sent
          # or when agent explicitly acknowledges
          count = 0
          for task_id in task_ids:
              # Remove from legacy llm_context location (migration)
              results = self._context.llm_context.get("background_results", [])
              self._context.llm_context["background_results"] = [
                  r for r in results if r.get("task_id") != task_id
              ]
              count += 1
          return count

  ---
  Component 5: Group Task Handling

  File: penguiflow/sessions/session.py

  async def _enqueue_group_proactive_report(
      self,
      group: TaskGroup,
      combined_patches: list[ContextPatch],
  ) -> None:
      """Queue a combined proactive report for a completed task group."""
      if not self._proactive_config or not self._proactive_config.get("enabled"):
          return

      # Combine digests from all patches
      combined_summary = []
      for patch in combined_patches:
          if patch.digest:
              combined_summary.extend(patch.digest)

      request = ProactiveReportRequest(
          task_id=f"group_{group.group_id}",
          session_id=self.session_id,
          group_id=group.group_id,
          task_description=f"Task group '{group.name}' ({len(group.task_ids)} tasks)",
          patch=ContextPatch(
              task_id=f"group_{group.group_id}",
              digest=combined_summary,
          ),
          merge_strategy=group.merge_strategy,
          # Short-term memory context
          memory_summary=dict(self._context.memory),
          tool_context=dict(self._context.tool_context),
          context_version=self._context.version,
          context_hash=self._context.context_hash,
          # Group-specific
          is_group_report=True,
          group_task_ids=list(group.task_ids),
          combined_patches=combined_patches,
      )
      # Use the same proactive reporter runner for group requests
      self._proactive_queue.put_nowait(request)

  NOTE: Group report processing should be handled by the same reporter loop
  (or a dedicated group runner that mirrors its semantics). Avoid maintaining
  a second queue without a consumer.

  ---
  Component 6: Acknowledgment Tool

  File: penguiflow/sessions/task_tools.py

  class AcknowledgeBackgroundArgs(BaseModel):
      """Arguments for acknowledging background task results."""
      task_ids: list[str] = Field(
          description="List of background task IDs to acknowledge as used"
      )


  class AcknowledgeBackgroundResult(BaseModel):
      """Result of acknowledging background results."""
      acknowledged: list[str]
      cleaned_up: int


  @tool(
      desc="Acknowledge that you have used background task results. "
           "Call this after you've incorporated the results into your response. "
           "This cleans up the results from context.",
      args=AcknowledgeBackgroundArgs,
  )
  async def acknowledge_background(
      args: AcknowledgeBackgroundArgs,
      ctx: ToolContext,
  ) -> AcknowledgeBackgroundResult:
      """Mark background task results as consumed and clean up."""
      session = _get_session(ctx)
      cleaned = await session.mark_background_consumed(task_ids=args.task_ids)
      return AcknowledgeBackgroundResult(
          acknowledged=args.task_ids,
          cleaned_up=cleaned,
      )

  ---
  Component 7: Proactive Reporting Setup (Required)

  Consumers must explicitly enable proactive reporting. This is NOT automatic.

  File: penguiflow/cli/playground.py (or orchestrator setup)

  async def setup_proactive_reporting(
      session: StreamingSession,
      planner_factory: Callable[[], ReactPlanner],
  ) -> None:
      """Enable proactive reporting with the library default generator."""
      generator = await create_default_proactive_generator(planner_factory, session)
      session.configure_proactive_reporting(
          generator=generator,
          enabled=True,
          timeout_s=60.0,
          max_hops=2,  # Safety net against recursive proactive loops
      )

  # Called during playground/orchestrator initialization:
  await setup_proactive_reporting(session, planner_factory)

  NOTE: Expose max_hops in template/spec generation so downstream apps can tune
  proactive recursion limits (default: 2).

  ---
  Component 8: LLM Context Handling

  File: penguiflow/planner/llm.py

  Background results must be EXTRACTED and REMOVED from llm_context before
  passing to build_user_prompt(). This follows the same pattern as conversation_memory
  and prevents double-rendering. Proactive runs should rely on short-term memory
  (conversation_memory) rather than full llm_context snapshots.

  async def build_messages(planner: Any, trajectory: Trajectory) -> list[dict[str, str]]:
      llm_context = trajectory.llm_context
      conversation_memory = None
      proactive_report = None
      background_results = None  # NEW

      if isinstance(llm_context, Mapping):
          # Existing: extract and remove conversation_memory
          if "conversation_memory" in llm_context:
              conversation_memory = llm_context.get("conversation_memory")
              llm_context = {k: v for k, v in llm_context.items() if k != "conversation_memory"}

          # Existing: extract proactive_report (kept for system prompt injection)
          if "proactive_report" in llm_context:
              proactive_report = llm_context.get("proactive_report")

          # NEW: extract and remove background_results (handled by proactive generator)
          if "background_results" in llm_context:
              background_results = llm_context.get("background_results")
              llm_context = {k: v for k, v in llm_context.items() if k != "background_results"}

      # ... rest of message building ...

  ---
  Lifecycle Summary

  ┌──────────────────────────────────────────────────────────────────────────┐
  │                        BACKGROUND RESULT LIFECYCLE                        │
  ├──────────────────────────────────────────────────────────────────────────┤
  │                                                                           │
  │  1. CREATED                                                               │
  │     └─ Background task completes                                          │
  │     └─ BackgroundTaskResult stored in trajectory.background_results       │
  │     └─ consumed = False                                                   │
  │                                                                           │
  │  2. QUEUED FOR REPORTING                                                  │
  │     └─ ProactiveReportRequest enqueued with short-term memory snapshot    │
  │     └─ For groups: waits until all tasks complete, then combined          │
  │                                                                           │
  │  3. PROACTIVE MESSAGE GENERATED                                           │
  │     └─ Default generator runs FULL planner loop                           │
  │     └─ Agent has access to toolbox + short-term memory context            │
  │     └─ Proactive hop guard enforced (default max=2)                        │
  │     └─ Natural message published as StateUpdate.RESULT                    │
  │                                                                           │
  │  4. CONSUMED & CLEANED UP                                                 │
  │     └─ Generator calls session.mark_background_consumed()                 │
  │     └─ consumed = True                                                    │
  │     └─ Result removed from trajectory.background_results                  │
  │     └─ No longer bloats context                                           │
  │                                                                           │
  │  Alternative path (no proactive):                                         │
  │  3b. AGENT USES RESULTS DIRECTLY                                          │
  │      └─ Agent reads from trajectory.background_results                    │
  │      └─ Agent calls acknowledge_background tool                           │
  │      └─ Same cleanup as step 4                                            │
  │                                                                           │
  └──────────────────────────────────────────────────────────────────────────┘

  ---
  Migration from Current System

  | Current                              | New                                               |
  |--------------------------------------|---------------------------------------------------|
  | llm_context["background_results"]    | trajectory.background_results                     |
  | No cleanup                           | consumed flag + cleanup on acknowledgment         |
  | No proactive generator               | Library default using full planner loop           |
  | enabled=False default                | enabled=False (explicit opt-in via setup helper)  |
  | No context in ProactiveReportRequest | Short-term memory + tool_context snapshot         |
  | Individual group task reports        | Combined single message for groups                |
  | background_results in user prompt    | Extracted + removed (handled by proactive gen)    |

  ---
  Backward Compatibility

  These changes maintain backward compatibility:

  1. **Proactive reporting stays disabled by default** - Tests that don't configure
     proactive reporting will continue to work. The reporter task only starts when
     explicitly enabled via configure_proactive_reporting(enabled=True).

  2. **New ProactiveReportRequest fields have defaults** - Existing code that creates
     ProactiveReportRequest without new fields will work (fields default to empty).

  3. **Trajectory serialization is additive** - Old serialized trajectories without
     background_results will deserialize correctly (field defaults to empty dict).

  4. **LLM context extraction is defensive** - Code checks for key existence before
     removing, so contexts without background_results work unchanged.

  Test Updates Required:

  | File                    | Line(s) | Change Required                                    |
  |-------------------------|---------|---------------------------------------------------|
  | test_context_merge.py   | 36      | Assert in trajectory instead of llm_context       |
  | test_context_merge.py   | 35-36   | Assert in trajectory instead of llm_context       |
  | test_task_service.py    | 92      | Assert in trajectory instead of llm_context       |

  ---
  Files to Modify

  | File                      | Changes                                                               |
  |---------------------------|-----------------------------------------------------------------------|
  | planner/trajectory.py     | Add BackgroundTaskResult, background_results field, consumption methods |
  | sessions/models.py        | Extend ProactiveReportRequest with context fields                     |
  | sessions/session.py       | Add context to request, mark_background_consumed, group handling      |
  | sessions/proactive.py     | NEW - Library default generator with full planner loop                |
  | sessions/task_tools.py    | Add acknowledge_background tool                                       |
  | planner/llm.py            | Extract+remove background_results from llm_context                    |
  | planner/prompts.py        | Add render_background_results() helper (optional)                     |
  | cli/playground.py         | Call setup_proactive_reporting() during initialization                |
  | cli/spec.py               | Add proactive max_hops config surfaced in templates/specs             |
  | cli/generate.py           | Wire max_hops into generated configs/templates                         |
  | tests/test_context_merge.py | Update assertions to check trajectory.background_results            |
  | tests/test_task_service.py  | Update assertions to check trajectory.background_results            |

  ---

concern: retain_turn (foreground waits for background) (need attention)

  - retain_turn only waits when a group exists and is sealed; without a sealed group it returns immediately, so “keep the turn
    until they finish” only works with group+sealed. penguiflow/sessions/task_service.py
  - retain_turn timeout is hardcoded (30s) and the planner config retain_turn_timeout_s isn’t used anywhere; background
    continuation hooks are defined but not wired. penguiflow/planner/models.py, penguiflow/sessions/task_service.py
  - even with retain_turn, background completion still queues proactive reports; once the foreground turn completes, the proactive
    reporter can emit a redundant update for results already included in the same reply. penguiflow/sessions/session.py