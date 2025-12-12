"""Prompt helpers for the React planner."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def render_summary(summary: Mapping[str, Any]) -> str:
    return "Trajectory summary: " + _compact_json(summary)


def render_resume_user_input(user_input: str) -> str:
    return f"Resume input: {user_input}"


def render_planning_hints(hints: Mapping[str, Any]) -> str:
    lines: list[str] = []
    constraints = hints.get("constraints")
    if constraints:
        lines.append(f"Respect the following constraints: {constraints}")
    preferred = hints.get("preferred_order")
    if preferred:
        lines.append(f"Preferred order (if feasible): {preferred}")
    parallels = hints.get("parallel_groups")
    if parallels:
        lines.append(f"Allowed parallel groups: {parallels}")
    disallowed = hints.get("disallow_nodes")
    if disallowed:
        lines.append(f"Disallowed tools: {disallowed}")
    preferred_nodes = hints.get("preferred_nodes")
    if preferred_nodes:
        lines.append(f"Preferred tools: {preferred_nodes}")
    budget = hints.get("budget")
    if budget:
        lines.append(f"Budget hints: {budget}")
    if not lines:
        return ""
    return "\n".join(lines)


def render_disallowed_node(node_name: str) -> str:
    return f"tool '{node_name}' is not permitted by constraints. Choose an allowed tool or revise the plan."


def render_ordering_hint_violation(expected: Sequence[str], proposed: str) -> str:
    order = ", ".join(expected)
    return f"Ordering hint reminder: follow the preferred sequence [{order}]. Proposed: {proposed}. Revise the plan."


def render_parallel_limit(max_parallel: int) -> str:
    return f"Parallel action exceeds max_parallel={max_parallel}. Reduce parallel fan-out."


def render_sequential_only(node_name: str) -> str:
    return f"tool '{node_name}' must run sequentially. Do not include it in a parallel plan."


def render_parallel_setup_error(errors: Sequence[str]) -> str:
    detail = "; ".join(errors)
    return f"Parallel plan invalid: {detail}. Revise the plan and retry."


def render_empty_parallel_plan() -> str:
    return "Parallel plan must include at least one branch in 'plan'."


def render_parallel_with_next_node(next_node: str) -> str:
    return f"Parallel plan cannot set next_node='{next_node}'. Use 'join' to continue or finish the run explicitly."


def render_parallel_unknown_failure(node_name: str) -> str:
    return f"tool '{node_name}' failed during parallel execution. Investigate the tool and adjust the plan."


def build_summarizer_messages(
    query: str,
    history: Sequence[Mapping[str, Any]],
    base_summary: Mapping[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a summariser producing compact JSON state. "
                "Respond with valid JSON matching the TrajectorySummary schema."
            ),
        },
        {
            "role": "user",
            "content": _compact_json(
                {
                    "query": query,
                    "history": list(history),
                    "current_summary": dict(base_summary),
                }
            ),
        },
    ]


def _compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def render_tool(record: Mapping[str, Any]) -> str:
    args_schema = _compact_json(record["args_schema"])
    out_schema = _compact_json(record["out_schema"])
    tags = ", ".join(record.get("tags", ()))
    scopes = ", ".join(record.get("auth_scopes", ()))
    parts = [
        f"- name: {record['name']}",
        f"  desc: {record['desc']}",
        f"  side_effects: {record['side_effects']}",
        f"  args_schema: {args_schema}",
        f"  out_schema: {out_schema}",
    ]
    if tags:
        parts.append(f"  tags: {tags}")
    if scopes:
        parts.append(f"  auth_scopes: {scopes}")
    if record.get("cost_hint"):
        parts.append(f"  cost_hint: {record['cost_hint']}")
    if record.get("latency_hint_ms") is not None:
        parts.append(f"  latency_hint_ms: {record['latency_hint_ms']}")
    if record.get("safety_notes"):
        parts.append(f"  safety_notes: {record['safety_notes']}")
    if record.get("extra"):
        parts.append(f"  extra: {_compact_json(record['extra'])}")
    return "\n".join(parts)


def build_system_prompt(
    catalog: Sequence[Mapping[str, Any]],
    *,
    extra: str | None = None,
    planning_hints: Mapping[str, Any] | None = None,
    current_date: str | None = None,
) -> str:
    """Build comprehensive system prompt for the planner.

    The library provides baseline behavior: context (including memories) is injected
    via the user prompt. Use `extra` to specify format-specific interpretation rules
    that your application requires.

    Args:
        catalog: Tool catalog (rendered tool specs)
        extra: Optional instructions for interpreting custom context structures.
               This is where you define how the planner should use memories or other
               domain-specific data passed via llm_context.

               Common patterns:
               - Memory as JSON object: "context.memories contains user preferences
                 as {key: value}; prioritize them when selecting tools."
               - Memory as text: "context.knowledge is free-form notes; extract
                 relevant facts as needed."
               - Historical context: "context.previous_failures lists failed attempts;
                 avoid repeating the same tool sequence."

        planning_hints: Optional planning constraints and preferences (ordering,
                       disallowed nodes, parallel limits, etc.)

        current_date: Optional date string (YYYY-MM-DD). If not provided, defaults
                     to today's date. Date-only (no time) for better LLM cache hits.

    Returns:
        Complete system prompt string combining baseline rules + tools + extra + hints
    """
    rendered_tools = "\n".join(render_tool(item) for item in catalog)

    # Default to current date if not provided (date-only for better cache hits)
    if current_date is None:
        from datetime import date

        current_date = date.today().isoformat()  # "YYYY-MM-DD"

    prompt_sections: list[str] = []

    # ─────────────────────────────────────────────────────────────
    # IDENTITY & ROLE
    # ─────────────────────────────────────────────────────────────
    prompt_sections.append(f"""<identity>
You are an autonomous reasoning agent that solves tasks by selecting and orchestrating tools.
Your name and voice on how to answer will come at the end of the prompt in additional_guidance.

Your role is to:
- Understand the user's intent and break complex queries into actionable steps
- Select appropriate tools from your catalog to gather information or perform actions
- Synthesize observations into clear, accurate answers
- Know when you have enough information to answer and when you need more

Current date: {current_date}
</identity>""")

    # ─────────────────────────────────────────────────────────────
    # OUTPUT FORMAT (NON-NEGOTIABLE)
    # ─────────────────────────────────────────────────────────────
    prompt_sections.append("""<output_format>
Think briefly in plain text, then respond with a single JSON object that matches the PlannerAction schema.

Write your JSON inside one markdown code block (```json ... ```).
Do not emit multiple JSON objects or extra commentary after the code block.
</output_format>""")

    # ─────────────────────────────────────────────────────────────
    # ACTION SCHEMA
    # ─────────────────────────────────────────────────────────────
    prompt_sections.append("""<action_schema>
Every response follows this structure:

{
  "thought": "Your reasoning about what to do next (required, keep concise)",
  "next_node": "tool_name" | null,
  "args": { ... } | null,
  "plan": [...] | null,
  "join": { ... } | null
}

Field meanings:
- thought: Brief explanation of your reasoning (1-2 sentences, factual)
- next_node: Name of the tool to call, or null when finished
- args: Arguments for the tool (when next_node is set) or final answer structure (when finished)
- plan: For parallel execution - list of {node, args} to run concurrently
- join: For parallel execution - how to combine results. If there is no join/aggregator tool in \
the catalog, combine the parallel outputs yourself in the final answer instead of calling a missing tool.
</action_schema>""")

    # ─────────────────────────────────────────────────────────────
    # FINISHING (CRITICAL)
    # ─────────────────────────────────────────────────────────────
    prompt_sections.append("""<finishing>
When you have gathered enough information to answer the query:

1. Set "next_node" to null
2. Provide "args" with this structure:

{
  "raw_answer": "Your complete, human-readable answer to the user's query"
}

The raw_answer field is REQUIRED. Write a full, helpful response - not a summary or fragment.
Focus on solving the user query, going to the point of answering what they asked.

Optional fields you may include in args:
- "confidence": 0.0 to 1.0 (your confidence in the answer's correctness)
- "route": category string like "knowledge_base", "calculation", "generation", "clarification"
- "requires_followup": true if you need clarification from the user
- "warnings": ["string", ...] for any caveats, limitations, or data quality concerns

Do NOT include heavy data (charts, files, large JSON) in args - artifacts from tool outputs are collected automatically.

Example finish:
{
  "thought": "I have analyzed the sales data and generated the chart. Ready to answer.",
  "next_node": null,
  "args": {
    "raw_answer": "Q4 2024 revenue increased 15% YoY to $1.2M. December was strongest.",
    "confidence": 0.92,
    "route": "analytics"
  }
}
</finishing>""")

    # ─────────────────────────────────────────────────────────────
    # TOOL USAGE
    # ─────────────────────────────────────────────────────────────
    prompt_sections.append("""<tool_usage>
Rules for using tools:

1. Only use tools listed in the catalog below - never invent tool names
2. Match your args to the tool's args_schema exactly
3. Consider side_effects before calling:
   - "pure": Safe to call multiple times, no external changes
   - "read": Reads external data but doesn't modify anything
   - "write": Modifies external state - use carefully
   - "external": Calls external services - may have rate limits or costs
4. Use the tool's description to understand when it's appropriate
5. If a tool fails, consider alternative approaches before giving up
</tool_usage>""")

    # ─────────────────────────────────────────────────────────────
    # PARALLEL EXECUTION
    # ─────────────────────────────────────────────────────────────
    prompt_sections.append("""<parallel_execution>
For tasks that benefit from concurrent execution, use parallel plans:

{
  "thought": "I need data from multiple independent sources",
  "next_node": null,
  "plan": [
    {"node": "tool_a", "args": {...}},
    {"node": "tool_b", "args": {...}}
  ],
  "join": {
    "node": "aggregator_tool",
    "args": {},
    "inject": {"results": "$results", "count": "$success_count"}
  }
}

Available injection sources for join.inject:
- $results: List of successful outputs
- $branches: Full branch details with node names
- $failures: List of failed branches with errors
- $success_count: Number of successful branches
- $failure_count: Number of failed branches
- $expect: Expected number of branches

Use parallel execution when:
- Multiple independent data sources need to be queried
- Multiple independent queries can be made to the same source in parallel
- Breakdown of multiples independent queries is more efficient than sequential calls
- A single query seems too difficult to answer directly and several simpler queries can help
- Tasks can be decomposed into non-dependent subtasks
- Speed matters and tools don't have ordering dependencies
</parallel_execution>""")

    # ─────────────────────────────────────────────────────────────
    # REASONING GUIDANCE
    # ─────────────────────────────────────────────────────────────
    prompt_sections.append("""<reasoning>
Approach problems systematically:

1. Understand first: Parse the query to identify what's actually being asked
2. Plan before acting: Consider which tools will help and in what order.
3. Gather evidence: Use tools to collect relevant information
4. Synthesize: Combine observations into a coherent answer
5. Verify: Check if your answer actually addresses the query

When uncertain:
- If you lack information to answer confidently, say so honestly
- If multiple interpretations exist, address the most likely one and note alternatives
- If a tool fails, explain what happened and try alternatives
- If you cannot complete the task, explain why and what would help

Avoid:
- Making up information not supported by tool observations
- Calling the same tool repeatedly with identical arguments
- Ignoring errors or unexpected results
- Providing partial answers without acknowledging gaps
</reasoning>""")

    # ─────────────────────────────────────────────────────────────
    # TONE & STYLE
    # ─────────────────────────────────────────────────────────────
    prompt_sections.append("""<tone>
In your raw_answer:
- Be direct and informative - get to the point
- Use clear, professional language
- Acknowledge limitations honestly rather than hedging excessively
- Match the formality level to the query (technical queries get technical answers)
- Avoid unnecessary caveats, but do note important limitations
- Don't apologize unless you've actually made an error
- These are safe defaults. Your tone or voice can be changed in the additional_guidance section.
- you can use markdown formatting if suggested in additional_guidance.

In your thought field:
- Be concise and factual
- Focus on reasoning, not commentary
- 1-2 sentences maximum
</tone>""")

    # ─────────────────────────────────────────────────────────────
    # ERROR HANDLING
    # ─────────────────────────────────────────────────────────────
    prompt_sections.append("""<error_handling>
When things go wrong:

Tool validation error: Fix your args to match the schema and retry
Tool execution error: Note the error, try alternative tools or approaches
No suitable tools: Explain what you cannot do and why
Ambiguous query: Make reasonable assumptions and note them, or ask for clarification
Conflicting information: Acknowledge the conflict and explain your reasoning

If you cannot complete the task after reasonable attempts:
- Set requires_followup: true in your finish args
- Explain what you tried and why it didn't work
- Suggest what additional information or tools would help
</error_handling>""")

    # ─────────────────────────────────────────────────────────────
    # AVAILABLE TOOLS
    # ─────────────────────────────────────────────────────────────
    no_tools_msg = "(No tools available - provide direct answers based on your knowledge)"
    tools_section = f"""<available_tools>
{rendered_tools if rendered_tools else no_tools_msg}
</available_tools>"""
    prompt_sections.append(tools_section)

    # ─────────────────────────────────────────────────────────────
    # ADDITIONAL GUIDANCE (USER-PROVIDED)
    # ─────────────────────────────────────────────────────────────
    if extra:
        prompt_sections.append(f"""<additional_guidance>
{extra}
</additional_guidance>""")

    # ─────────────────────────────────────────────────────────────
    # PLANNING HINTS
    # ─────────────────────────────────────────────────────────────
    if planning_hints:
        rendered_hints = render_planning_hints(planning_hints)
        if rendered_hints:
            prompt_sections.append(f"""<planning_constraints>
{rendered_hints}
</planning_constraints>""")

    return "\n\n".join(prompt_sections)


def build_user_prompt(query: str, llm_context: Mapping[str, Any] | None = None) -> str:
    """Build user prompt with query and optional LLM context.

    This is the baseline mechanism for injecting memories and other context into
    the planner. The structure/format is developer-defined; use system_prompt_extra
    to document interpretation semantics if needed.

    Args:
        query: The user's question or request
        llm_context: Optional context visible to LLM. Can contain memories,
                    status_history, knowledge bases, or any custom structure.
                    Should NOT include internal metadata like tenant_id or trace_id.

                    Examples:
                    - {"memories": {"user_pref_lang": "python"}}
                    - {"knowledge": "User prefers concise answers."}
                    - {"previous_failures": ["tool_a timed out", "tool_b invalid args"]}

    Returns:
        JSON string with query and context
    """
    if llm_context:
        # Filter out 'query' if present to avoid duplication
        context_dict = {k: v for k, v in llm_context.items() if k != "query"}
        if context_dict:
            return _compact_json({"query": query, "context": context_dict})
    return _compact_json({"query": query})


def render_observation(
    *,
    observation: Any | None,
    error: str | None,
    failure: Mapping[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {}
    if observation is not None:
        payload["observation"] = observation
    if error:
        payload["error"] = error
    if failure:
        payload["failure"] = dict(failure)
    if not payload:
        payload["observation"] = None
    return _compact_json(payload)


def render_hop_budget_violation(limit: int) -> str:
    return (
        "Hop budget exhausted; you have used all available tool calls. "
        "Finish with the best answer so far or reply with no_path."
        f" (limit={limit})"
    )


def render_deadline_exhausted() -> str:
    return "Deadline reached. Provide the best available conclusion or return no_path."


def render_validation_error(node_name: str, error: str) -> str:
    return f"args for tool '{node_name}' did not validate: {error}. Return corrected JSON."


def render_output_validation_error(node_name: str, error: str) -> str:
    return (
        f"tool '{node_name}' returned data that did not validate: {error}. "
        "Ensure the tool output matches the declared schema."
    )


def render_invalid_node(node_name: str, available: Sequence[str]) -> str:
    options = ", ".join(sorted(available))
    return f"tool '{node_name}' is not in the catalog. Choose one of: {options}."


def render_invalid_join_injection_source(source: str, available: Sequence[str]) -> str:
    options = ", ".join(available)
    return f"join.inject uses unknown source '{source}'. Choose one of: {options}."


def render_join_validation_error(node_name: str, error: str, *, suggest_inject: bool) -> str:
    message = f"args for join tool '{node_name}' did not validate: {error}. Return corrected JSON."
    if suggest_inject:
        message += " Provide 'join.inject' to map parallel outputs to this join tool."
    return message


def render_repair_message(error: str) -> str:
    return (
        "Previous response was invalid JSON or schema mismatch: "
        f"{error}. Reply with corrected JSON only. "
        "When finishing, set next_node to null and include raw_answer in args."
    )
