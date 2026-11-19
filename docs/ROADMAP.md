# PenguiFlow Roadmap 2026

> **Status:** Living document — updated May 2026

---

## Roadmap

| Theme | Description | Impact if not accomplished | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Ongoing | Done |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Migration to Agents that reason dynamically (not fixed workflows as today)** | Agents that plan and adapt autonomously instead of following rigid scripts → unlocks complex, multi-step reasoning tasks (research, analysis, complex-queries resolution) without hand-coding every path. | Without it: users must pre-program every scenario, making agents brittle and unscalable. | ● | ● | | | | | | | | | | | | ✅ |
| **Industry-standard tool integrations (MCP + UTCP)** | Connect to MCP servers and any REST API via UTCP without writing adapters → turns agents into universal integrators. | Without it: each tool needs custom code, blocking adoption of the growing MCP ecosystem. | ● | ● | | | | | | | | | | | | ✅ |
| **Track agent reasoning step-by-step** | Full transparency into WHY an agent took each action → trust, debugging, audit trails for regulated environments. | Without it: agents are black boxes, unacceptable for compliance-sensitive industries. | ● | ● | | | | | | | | | | | | ✅ |
| **Run tools in parallel (faster, cheaper)** | Execute independent tool calls simultaneously → 2-5x faster task completion, lower LLM costs from fewer round-trips. | Without it: users wait minutes for sequential tool execution. | ● | ● | | | | | | | | | | | | ✅ |
| **Auto-recover from errors (reliability)** | Self-healing when tools fail or LLMs produce invalid output → agents complete tasks without human babysitting. | Without it: every transient failure kills the task, requiring manual restart. | ● | ● | | | | | | | | | | | | ✅ |
| **Conversation continuity across all agents (Migrated from Reporting Agent)** | Users carry context across agent interactions without restating goals → feels like a single intelligent assistant, not disjoint sessions. | Without it: users re-explain context constantly, creating friction. | ● | ● | ● | | | | | | | | | | | ✅ |
| **Unified State Store (Single source of truth for agent state)** | Single durable source of truth for all agent state → enables pause/resume, crash recovery, audit trails. | Without it: agent state lives in-memory, lost on restart — no production reliability. | | ● | ● | | | | | | | | | | | ✅ |
| **Control memory costs per agent** | Configurable memory budgets and rolling summarization → predictable token/spend per agent, no unbounded context growth. | Without it: memory costs spiral out of control with long-running agents. | | ● | ● | ● | | | | | | | | | | ✅ |
| **Skills and documentation** | Common patterns documented as 16 ready-to-use skills to accelerate time to deployment. | Without it: every project starts from scratch, inconsistent architecture. | ● | ● | ● | | | | | | | | | | | ✅ |
| **See agent thinking in real-time (debugging)** | Live streaming of agent reasoning + tool calls to a UI client → instant debugging, no more reading raw logs. | Without it: developers debug blindly with post-hoc log analysis. | | ● | ● | | | | | | | | | | | ✅ |
| **Background tasks** | Launch analysis, close laptop, get notified when done → agents work async, users stay productive. | Without it: users stare at progress spinners for long-running tasks. | | ● | ● | ● | ● | | | | | | | | | ✅ |
| **Concurrent execution control (Prevent system overload)** | Throttle simultaneous tool/agent executions → prevents API rate limits, OOM crashes, runaway costs. | Without it: a single heavy task can degrade the entire system. | | | | ● | ● | | | | | | | | | ✅ |
| **Task groups** | Correlate related background tasks into unified reports → users get consolidated results instead of scattered notifications. | Without it: users manually piece together outputs from parallel tasks. | | ● | ● | ● | ● | | | | | | | | | ✅ |
| **Proactive report back** | Agents notify users when background work completes → no polling, no "is it done yet?" Long lasting jobs (ML Workflows / Forecasting / Deep nested queries) are candidates to be background task and get notified when results are ready, without breaking conversation. | Without it: users must manually check task status or miss completion entirely. | | | | | ● | ● | | | | | | | | 🔲 |
| **Pause and resume agent work without losing progress** | Interrupt long-running agents, come back later without losing progress → works around human attention spans and schedules. | Without it: users must restart from scratch after interruptions. | | | | ● | ● | ● | | | | | | | | ✅ |
| **Human-in-the-loop approval for sensitive actions** | Sensitive actions require human approval → safety for production, compliance for sensitive workflows. | Without it: autonomous agents can't be trusted with real-world actions. | | ● | ● | ● | ● | ● | | | | | | | | ✅ |
| **Scheduled reports (daily alerts, weekly summaries)** | Agents run analysis on a cron schedule (daily alerts, weekly summaries) → automated recurring intelligence. | Without it: users manually trigger recurring analyses or build external cron infra. | | | ● | ● | ● | ● | | | | | | | | ✅ |
| **User notification system (configurable alerts)** | Configurable alert channels (in-app, email, slack) → users choose how and when they're reached. | Without it: alerts are either silent or noisy with no user control. | | | | | | ● | ● | | | | | | | 🔲 |
| **Tasks complete even if system restarts (Resilience in background tasks)** | Background jobs survive process restarts via durable state → zero data loss from crashes or deploys. | Without it: any deployment kills in-flight tasks, breaking trust. | | | ● | ● | ● | | | | | | | | | ✅ |
| **Real-time voice infrastructure (LiveKit / Live APIs industry standards)** | LiveKit-based real-time audio transport → enables natural voice conversations with agents. | Without it: no voice interface possible. | | | | | | ● | ● | | | | | | | 🔲 |
| **Voice integration** | Connect voice I/O to agent pipeline → users speak naturally instead of typing. | Without it: limited to text-only interaction. | | | | | | ● | ● | ● | | | | | | 🔲 |
| **STT integration (hear users)** | Speech-to-text so agents can hear users → enables hands-free operation for field workers, accessibility. | Without it: voice agents can't understand spoken input. | | | | | | ● | ● | ● | | | | | | 🔲 |
| **TTS pipeline (agents speak back)** | Text-to-speech so agents speak back → natural auditory responses, accessibility for visually impaired. | Without it: voice interaction is one-way (user talks, reads responses). | | | | | | ● | ● | ● | | | | | | 🔲 |
| **Voice interrupt handling (Interrupt agent mid-sentence naturally)** | Agents stop mid-sentence when user interrupts → natural conversation flow, not talking over each other. | Without it: voice interaction feels robotic and frustrating. | | | | | | | | ● | ● | | | | | 🔲 |
| **Confidence-based confirmation ("did you say X?")** | "Did you say X?" when uncertain → reduces errors from ASR mistakes in noisy environments. | Without it: voice commands execute incorrectly without review. | | | | | | | | ● | ● | | | | | 🔲 |
| **Seamless mode switching (voice ↔ text)** | Switch between voice and text mid-conversation → users use whatever modality fits the moment. | Without it: users are locked into one modality per session. | | | | | | | ● | ● | ● | | | | | 🔲 |
| **Voice + visual (spoken commentary on charts)** | Spoken commentary on charts, images, screens → agents narrate visual data for presentations, accessibility. | Without it: voice agents can't describe or discuss visual content. | | | | | | | ● | ● | ● | | | | | 🔲 |
| **A2A protocol (Agent-to-agent collaboration)** | Standardized Google A2A spec for agent-to-agent communication: HTTP+JSON and gRPC bindings, SSE streaming, full task lifecycle, push notifications, cancel propagation. | Without it: agents are isolated islands, can't compose capabilities. | | ● | ● | ● | ● | ● | | | | | | | | ✅ |
| **Remote Agent Discovery (Find the right agent for each task)** | Find the right agent by capability scoring, not hardcoded address → dynamic delegation to best-fit specialists. Shipped with A2A router in production. (Shipped but we still have to register all available agents to the router agent) | Without it: manual wiring of agent dependencies, fragile topology. | | | ● | ● | ● | ● | | | | | | | | ✅ |
| **Cross-agent context sharing (Agents share context, sensitive data stays protected)** | Share context via task subscriptions while keeping sensitive data compartmentalized → efficient collaboration with proper access control. | Without it: agents either duplicate context or leak data. | | | | ● | ● | ● | ● | | | | | | | ✅ |
| **A2A observability (Track work across multiple agents)** | End-to-end visibility across multi-agent workflows with task subscription → track a single request through 20 agents. | Without it: distributed agent workflows are a black box. | | | | ● | ● | ● | ● | | | | | | | ✅ |
| **Multi-agent workflow skills** | Reusable orchestration patterns for common multi-agent scenarios (handoff, broadcast, supervisor/worker, debate) → teams don't reinvent collaboration logic. | Without it: every multi-agent workflow is bespoke, high-risk code. | | | | ● | ● | ● | | | | | | | | ✅ |
| **Parallel agent execution (Multiple agents work simultaneously)** | Multiple agents work simultaneously on independent sub-tasks → total time = slowest sub-task, not sum of all. | Without it: multi-agent workflows are sequential, defeating the purpose. | | | | ● | ● | | | | | | | | | ✅ |
| **Human-in-the-loop for multi-agent decisions** | Human approves or picks between agent proposals → safety in complex autonomous systems. | Without it: multi-agent delegation operates with no human oversight. | | | | ● | ● | ● | | | | | | | | ✅ |
| **Agent registry (Central catalog of available agents)** | Central catalog of all available agents with capabilities, health, pricing → governance and discovery at scale. | Without it: teams don't know what agents exist or whether they're healthy. | | | | ● | ● | | | | | | | | | 🔲 |
| **Rate limiting & Prevent runaway agent chains** | Circuit breakers, cost caps, recursion limits → prevents infinite agent loops and budget blowups. | Without it: one buggy agent can trigger a cost explosion across the fleet. | | | | ● | ● | ● | | | | | | | | 🔲 |
| **Routing Agent — POC then sub-agents as skills in Dynamic Routing Agent** | Meta-agent that analyzes requests and routes to sub-agents as skills via A2A → single entry point, users don't need to know which agent to call. Shipped up-to RC1. | Without it: users must know which agent to call — defeats "just ask." user experience and isolates knowledge. | | | | ● | ● | ● | | | | | | | | ✅ |

---

## Jira Tickets — April to June Work

**USA holidays excluded (May 25 Memorial Day, Jun 19 Juneteenth).** April and May each < 160h actuals. June ~ 160h projected.

---

### ✅ DONE — Tickets span their full active period per roadmap

---

#### PF-001: Concurrent Execution Control
**Active:** Apr–May | **Estimate:** 24h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Per-agent concurrency semaphore with configurable max | 8h | Apr 8 (Wed) |
| Global rate limiter (token bucket / sliding window) with queue backpressure | 8h | Apr 9–10 (Thu–Fri) |
| ToolNode + A2A router integration for throttled dispatch | 8h | Apr 13 (Mon) |

---

#### PF-002: Pause and Resume Agent Work
**Active:** Apr–Jun | **Estimate:** 24h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| ReactPlanner pause/resume extension for human intervention | 8h | Apr 14–15 (Tue–Wed) |
| State persistence + conversation rehydration on resume | 8h | Apr 16–17 (Thu–Fri) |
| Stream cancel propagation + manual resume flow | 8h | May 4–5 (Mon–Tue) |

---

#### PF-003: Human-in-the-Loop Approval for Sensitive Actions
**Active:** Feb–Jun | **Estimate:** 40h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Steering hooks + guard inbox for approval workflows | 12h | Feb 23–25 (Mon–Wed) |
| Approval decision flow (review, approve, reject, modify) | 12h | Mar 9–11 (Mon–Wed) |
| A2A input-required / auth-required lifecycle mapping | 8h | Apr 20–21 (Mon–Tue) |
| Test coverage + edge case hardening for all approval paths | 8h | May 18–19 (Mon–Tue) |

---

#### PF-004: Scheduled Reports
**Active:** Mar–Jun | **Estimate:** 32h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Persistent StateStore capability for scheduled task storage | 8h | Mar 23–24 (Mon–Tue) |
| Agent meta-tools (schedule, list, pause, resume, delete) | 12h | Apr 27–29 (Mon–Wed) |
| Cron expression parsing + execution loop with catchup policy | 8h | May 6–7 (Wed–Thu) |
| Schedule persistence reload on system restart | 4h | Jun 5 (Fri) |

---

#### PF-005: Tasks Survive System Restart
**Active:** Mar–May | **Estimate:** 16h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| StateStore-backed run persistence with idempotent run records | 8h | Mar 30–31 (Mon–Tue) |
| Lease-based execution + crash recovery on restart | 8h | Apr 1–2 (Wed–Thu) |

---

#### PF-006: A2A Protocol — Full Google A2A Spec
**Active:** Feb–Jun | **Estimate:** 48h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| HTTP+JSON binding + task lifecycle API (send, get, cancel) | 12h | Feb 9–11 (Mon–Wed) |
| SSE streaming for real-time task updates | 8h | Mar 2–3 (Mon–Tue) |
| gRPC binding + protobuf stubs | 12h | Apr 6–8 (Mon–Wed) |
| Push notification config + cancellation propagation | 8h | May 11–12 (Mon–Tue) |
| Remote task progress sink + full spec compliance pass | 8h | Jun 1–2 (Mon–Tue) |

---

#### PF-007: Remote Agent Discovery & Registration
**Active:** Mar–Jun | **Estimate:** 24h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Agent capability scoring model + query API | 8h | Mar 16–17 (Mon–Tue) |
| Health check protocol + stale agent cleanup | 8h | Apr 22–23 (Wed–Thu) |
| Router agent registration pipeline + agent metadata indexing | 8h | May 26–27 (Tue–Wed) |

---

#### PF-008: Cross-Agent Context Sharing
**Active:** Apr–Jul | **Estimate:** 24h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Task subscription protocol + StateStore conversation bindings | 8h | Apr 6–7 (Mon–Tue) |
| Sensitive data compartmentalization with access control | 8h | Apr 27–28 (Mon–Tue) |
| Multi-turn continuity acceptance tests | 8h | May 13–14 (Wed–Thu) |

---

#### PF-009: A2A Observability
**Active:** Apr–Jul | **Estimate:** 24h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Multi-agent FlowEvent types (remote_binding_reuse, remote_task_poll, a2a_pause, remote_failover) | 8h | Apr 14–15 (Tue–Wed) |
| Trajectory aggregation for cross-agent timelines | 8h | May 18–19 (Mon–Tue) |
| Remote binding event logging + failover trace aggregation | 8h | Jun 15–16 (Mon–Tue) |

---

#### PF-010: Multi-Agent Workflow Skills
**Active:** Apr–Jun | **Estimate:** 24h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Handoff pattern (agent A delegates to agent B) + broadcast pattern | 12h | Apr 21–23 (Tue–Thu) |
| Supervisor/worker pattern + debate/ensemble pattern | 12h | May 11–12, 18 (Mon–Tue, Mon) |

---

#### PF-011: Parallel Agent Execution
**Active:** Apr–May | **Estimate:** 8h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| A2A router parallel dispatch + concurrent result aggregation | 8h | Apr 29–30 (Wed–Thu) |

---

#### PF-012: HITL for Multi-Agent Decisions
**Active:** Apr–Jun | **Estimate:** 24h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Multi-agent delegation approval workflow (select → pause → confirm → execute) | 8h | Apr 30–May 1 (Thu–Fri) |
| A2A router HITL integration + review prompt | 8h | May 25–26 (Mon–Tue) |
| Test coverage: multi-agent rejection, timeout, delegate-failover | 8h | Jun 3–4 (Wed–Thu) |

---

#### PF-013: Routing Agent RC1
**Active:** Apr–Jun | **Estimate:** 40h | **Status:** ✅ RC1 shipped

| Subtask | Hours | Date |
|---|---|---|
| Meta-agent request analysis + intent classification for routing | 12h | Apr 1–3 (Wed–Fri) |
| Agent selection logic + skill-to-agent mapping via A2A | 12h | Apr 6–8 (Mon–Wed) |
| Dynamic sub-agent invocation + result aggregation | 8h | May 4–5 (Mon–Tue) |
| RC1 stabilization: error handling, fallback, edge cases | 8h | Jun 8–9 (Mon–Tue) |

---

#### PF-014: Background Tasks — Completion & Polish
**Active:** Feb–May | **Estimate:** 24h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Active reporting + artifact return from background jobs | 8h | Feb 16–17 (Mon–Tue) |
| retain_turn config + proactive emission de-duplication | 8h | Mar 30–31 (Mon–Tue) |
| Background result prompt-bloat protection (extract/remove pattern) | 8h | Apr 20–21 (Mon–Tue) |

---

#### PF-015: Task Groups — Completion & Polish
**Active:** Feb–May | **Estimate:** 16h | **Status:** ✅ Done

| Subtask | Hours | Date |
|---|---|---|
| Task group correlation ID propagation across background jobs | 8h | Feb 23–24 (Mon–Tue) |
| Consolidated report delivery to the active session | 8h | Apr 22–23 (Wed–Thu) |

---

### 🔲 NOT DONE — Active across Apr–Jun, work ongoing

---

#### PF-016: Proactive Report Back
**Active:** May–Jun | **Remaining:** 24h | **Status:** 🔲 In progress

| Subtask | Hours | Date |
|---|---|---|
| Background task completion event + notification trigger | 8h | May 25–26 (Tue–Wed) |
| Result delivery to active conversation without breaking context | 8h | Jun 10–11 (Wed–Thu) |
| Configurable notification preferences per job type | 8h | Jun 22–23 (Mon–Tue) |

---

#### PF-017: User Notification System
**Active:** Jun–Jul | **Estimate:** 24h | **Status:** 🔲 Not started

| Subtask | Hours | Date |
|---|---|---|
| AlertSink / DeliverySink protocol + in-app notification pane | 12h | Jun 17–19 (Wed–Fri) |
| Email plugin (SMTP) + Slack plugin (webhook) + user prefs | 12h | Jun 22–24 (Mon–Wed) |

---

#### PF-018: Rate Limiting & Runaway Prevention
**Active:** Apr–Jun | **Remaining:** 24h | **Status:** 🔲 In progress

| Subtask | Hours | Date |
|---|---|---|
| Per-agent cost cap (tokens + USD) — initial work | 8h | Apr 27–28 (Mon–Tue) |
| Recursion depth limit — initial work | 8h | May 25–26 (Tue–Wed) |
| Chain-wide circuit breaker + alerting at 80/90/100% thresholds | 8h | Jun 25–26 (Thu–Fri) |

---

#### PF-019: Agent Registry
**Active:** Apr–Jun | **Remaining:** 16h | **Status:** 🔲 In progress

| Subtask | Hours | Date |
|---|---|---|
| Agent metadata schema + CRUD API — initial work | 8h | Apr 15–16 (Wed–Thu) |
| Health check protocol + registry browse API | 8h | Jun 29–30 (Mon–Tue) |

---

#### PF-020: Real-Time Voice Infrastructure (LiveKit)
**Active:** Jun–Jul | **Estimate:** 24h | **Status:** 🔲 Not started

| Subtask | Hours | Date |
|---|---|---|
| LiveKit server deployment + Python SDK integration | 12h | Jun 8–9 (Mon–Tue) |
| Bidirectional audio stream + connection lifecycle | 12h | Jun 10–11 (Wed–Thu) |

---

#### PF-021: Voice Integration
**Active:** Jun–Aug | **Estimate:** 24h | **Status:** 🔲 Not started

| Subtask | Hours | Date |
|---|---|---|
| Voice I/O wiring to agent message pipeline | 12h | Jun 15–16 (Mon–Tue) |
| Audio stream ↔ agent message conversion | 12h | Jun 17–18 (Wed–Thu) |

---

#### PF-022: STT Integration (hear users)
**Active:** Jun–Aug | **Estimate:** 16h | **Status:** 🔲 Not started

| Subtask | Hours | Date |
|---|---|---|
| STT provider integration (Deepgram / Whisper / AssemblyAI) | 8h | Jun 22–23 (Mon–Tue) |
| Real-time transcription with language detection + confidence scoring | 8h | Jun 24–25 (Wed–Thu) |

---

#### PF-023: TTS Pipeline (agents speak back)
**Active:** Jun–Aug | **Estimate:** 16h | **Status:** 🔲 Not started

| Subtask | Hours | Date |
|---|---|---|
| TTS provider integration (ElevenLabs / Cartesia / Play.ht) | 8h | Jun 29–30 (Mon–Tue) |
| Voice selection, speed/pitch config, SSML support | 8h | Jul 1–2 (Wed–Thu) |

---

#### PF-024: Voice Interrupt Handling
**Active:** Aug–Sep | **Estimate:** 16h | **Status:** 🔲 Not started

| Subtask | Hours | Date |
|---|---|---|
| Barge-in detection + turn-taking state machine | 8h | Aug 3–4 (Mon–Tue) |
| Agent mid-sentence stop + context preservation | 8h | Aug 5–6 (Wed–Thu) |

---

#### PF-025: Confidence-Based Confirmation
**Active:** Aug–Sep | **Estimate:** 16h | **Status:** 🔲 Not started

| Subtask | Hours | Date |
|---|---|---|
| Low-confidence detection + "Did you say X?" confirmation flow | 8h | Aug 10–11 (Mon–Tue) |
| Confirmation timeouts + fallback to text | 8h | Aug 12–13 (Wed–Thu) |

---

#### PF-026: Seamless Mode Switching (voice ↔ text)
**Active:** Jul–Sep | **Estimate:** 16h | **Status:** 🔲 Not started

| Subtask | Hours | Date |
|---|---|---|
| Session modality toggle + context continuity across modes | 8h | Jul 6–7 (Mon–Tue) |
| Mode indicator events + manual switch for UI clients | 8h | Jul 8–9 (Wed–Thu) |

---

#### PF-027: Voice + Visual (spoken commentary on charts)
**Active:** Jul–Sep | **Estimate:** 16h | **Status:** 🔲 Not started

| Subtask | Hours | Date |
|---|---|---|
| Chart/image data → natural language description pipeline | 8h | Jul 13–14 (Mon–Tue) |
| Voice narration + visual highlight synchronization | 8h | Jul 15–16 (Wed–Thu) |

---

## Capacity Overview

```
April 2026 (22 working days, <160h actual)
├── PF-001: Concurrent Execution Control (Apr 8–13)
├── PF-002: Pause and Resume (Apr 14–17)
├── PF-006: A2A Protocol gRPC (Apr 6–8)
├── PF-008: Cross-Agent Context Sharing (Apr 6–7, 27–28)
├── PF-009: A2A Observability (Apr 14–15)
├── PF-010: Multi-Agent Workflow Skills (Apr 21–23)
├── PF-011: Parallel Agent Execution (Apr 29–30)
├── PF-012: HITL Multi-Agent (Apr 30–May 1)
├── PF-013: Routing Agent (Apr 1–8)
├── PF-019: Agent Registry partial (Apr 15–16)
├── PF-018: Rate Limiting partial (Apr 27–28)
└── PF-005: Tasks Survive Restart (Apr 1–2)

May 2026 (20 working days, <160h actual)
├── PF-002: Pause and Resume (May 4–5)
├── PF-003: HITL Approval test pass (May 18–19)
├── PF-004: Scheduled Reports (May 6–7)
├── PF-006: A2A push notification (May 11–12)
├── PF-007: Remote Agent Discovery (May 26–27)
├── PF-008: Context Sharing tests (May 13–14)
├── PF-009: A2A Observability trajectory (May 18–19)
├── PF-012: HITL Multi-Agent (May 25–26)
├── PF-013: Routing Agent invocation (May 4–5)
├── PF-016: Proactive Report Back partial (May 25–26)
├── PF-018: Rate Limiting partial (May 25–26)
│   [May 25: Memorial Day]
└── PF-014: Background Tasks polish (May earlier)

June 2026 (21 working days, ~160h projected)
├── PF-004: Scheduled Reports persist reload (Jun 5)
├── PF-006: A2A full spec pass (Jun 1–2)
├── PF-009: A2A Observability event logging (Jun 15–16)
├── PF-012: HITL Multi-Agent tests (Jun 3–4)
├── PF-013: Routing Agent RC1 stabilization (Jun 8–9)
├── PF-016: Proactive Report Back (Jun 10–11, 22–23)
├── PF-017: Notification System (Jun 17–24)
├── PF-018: Rate Limiting circuit breaker (Jun 25–26)
├── PF-019: Agent Registry browse API (Jun 29–30)
├── PF-020: LiveKit Infrastructure (Jun 8–11)
├── PF-021: Voice Integration (Jun 15–18)
├── PF-022: STT Integration (Jun 22–25)
└── PF-023: TTS Pipeline (Jun 29–30)
    [Jun 19: Juneteenth]
```

---

## Hours Summary

| Month | Working Days | Hours | Status |
|---|---|---|---|
| **April** | 22 | < 160h | ✅ Actual — all Done |
| **May** | 20 | < 160h | ✅ Actual — all Done |
| **June** | 21 | ~ 160h projected | 🔲 In progress |
| **July** | 23 | ~ 160h projected | 🔲 Planned |
| **August** | 21 | ~ 160h projected | 🔲 Planned |
| **September** | 22 | ~ 160h projected | 🔲 Planned |

---

## Quality & Evaluation Pipeline

### Roadmap

| Theme | Description | Impact if not accomplished | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Ongoing | Done |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Judge agent (reflection loop)** | LLM-as-judge evaluates agent responses for accuracy, completeness, and adherence to instructions. Reflection loop retries or escalates low-scoring outputs before delivering to user. | Without it: agents deliver incorrect or low-quality responses with no self-correction — users lose trust in outputs. | | | | ● | ● | ● | | | | | | | | ✅ |
| **Define scoring criteria (accuracy, completeness, tone)** | Formal scoring rubrics per output type (reports, analysis, code, summaries). Criteria are versioned, reviewable, and domain-configurable. | Without it: evaluation is ad-hoc and inconsistent — "good enough" means different things to different users. | | | | | | ● | | | | | | | | 🔲 |
| **Store evaluation history for analysis** | Every eval score, judge rationale, and outcome persisted in StateStore. Queryable by agent, use case, date range, and score threshold. | Without it: you can't track quality trends, identify regressions, or prove improvement over time. | | | | | | ● | ● | ● | | | | | | 🔲 |
| **Track what works and what fails (tools, decisions)** | Per-tool success/failure rates, decision-path analysis, and common failure patterns surfaced in observability tooling. | Without it: you don't know which tools or reasoning patterns cause failures — improvements are guesswork. | | | | | | | ● | ● | | | | | | 🔲 |
| **Domain experts validate quality criteria** | Subject matter experts review and approve scoring rubrics per domain (finance, healthcare, engineering, etc.) via a validation workflow. | Without it: scoring criteria lack domain authority — agents may pass generic checks but fail domain-specific needs. | | | | | | | | ● | | | | | | 🔲 |
| **Categorize questions by complexity (Easy, Medium, Hard)** | Incoming queries classified by complexity to route to appropriate models and depth of reasoning. Complexity taxonomy is data-driven and adjustable. | Without it: simple queries waste expensive reasoning and hard queries get shallow answers — bad cost/quality tradeoff. | | | | | | | | ● | ● | ● | | | | 🔲 |
| **Escalate disagreements to human review** | When judge agent and primary agent disagree on quality, or confidence is low, escalate to a human for final decision. | Without it: the system makes final quality calls autonomously — high-risk for regulated or client-facing outputs. | | | | | | | | | ● | | | | | 🔲 |
| **Use cheaper models for obvious cases, expensive for hard ones** | LLM routing tier: easy queries → cheap/fast model, hard queries → expensive/capable model. Routing based on complexity classification. | Without it: you pay premium for every query — costs 3-10x more than necessary without quality benefit for simple cases. | | | | | | | | ● | ● | ● | | | | 🔲 |
| **Automatic dataset generation for Prompt Optimization** | Extract trajectory traces, score them, auto-generate labeled datasets for prompt tuning. Aligns with `docs/proposals/RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md`. | Without it: prompt optimization depends on manually curated examples — slow, biased, doesn't scale. | ● | ● | ● | ● | | | | | | | | | | ✅ |
| **Prompt Optimization (Improve agent instructions automatically)** | Automated prompt refinement using eval feedback. System proposes prompt changes, validates against dataset, deploys if scores improve. | Without it: prompt engineering is manual trial and error — fragile, time-consuming, and person-dependent. | | | | ● | ● | ● | ● | ● | | | | | | 🔲 |
| **Pre-built quality templates by use case** | Ready-to-use eval templates per domain: reports, analysis, code review, summarization, customer response. Each includes scoring criteria, complexity presets, and judge prompts. | Without it: every team builds evaluation from scratch — inconsistent, slow, and missing edge cases. | | | | | | | ● | ● | ● | ● | | | | 🔲 |
| **Quality dashboards (pass rates, common failures)** | Visual analytics: pass rates over time, failure distribution by category, model comparison, cost-per-quality-point. | Without it: quality is invisible — you can't demonstrate improvement to stakeholders or catch regressions early. | | | | | | | | | | | ● | ● | ● | 🔲 |
| **Agents generation/optimization MCP** | MCP server that exposes agent generation and optimization as a tool. Agents can create, evaluate, and improve other agents programmatically. | Without it: agent creation and tuning are manual dev tasks — can't scale to hundreds of specialized agents. | | | | | | | ● | ● | ● | ● | ● | ● | | 🔲 |
| **System learns quality standards per use case** | ML-based quality standard learning: system observes eval outcomes and human feedback, builds a model of "good enough" per use case, auto-adjusts scoring thresholds. | Without it: quality thresholds are static — they don't adapt to evolving user expectations or domain shifts. | | | | | | | | | | | ● | ● | ● | 🔲 |

---

### Breakdown by Item

#### QE-001: Judge Agent (Reflection Loop)
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow |
| **Status** | ✅ Done (built into ReactPlanner) |
| **Active** | Apr–Jun |
| **Description** | LLM-as-judge evaluates each agent response for accuracy, completeness, tone, and instruction adherence. Low-scoring outputs trigger a reflection loop where the agent revises its answer. If score remains below threshold after N retries, escalates to human review. |
| **Impact if not done** | Agents deliver incorrect or low-quality responses with no self-correction — users lose trust in outputs. Without reflection, every error reaches the user. |
| **Sample tools** | `ReflectionConfig(enabled=True, quality_threshold=0.8, max_revisions=2, criteria=ReflectionCriteria(...))` → `penguiflow/planner/reflection_prompts.py` ships the critique prompts. Passed to the `ReactPlanner(...)` constructor as `reflection_config=ReflectionConfig(...)` (set `use_separate_llm=True` + `reflection_llm=...` for a separate critique model). The structured critique is returned as `ReflectionCritique` (`score`, `passed`, `feedback`, `issues`, `suggestions`). |
| **Other tasks** | PF-003 (HITL for escalations), QE-002 (Scoring criteria definition), QE-005 (Domain expert validation) |

---

#### QE-002: Define Scoring Criteria (Accuracy, Completeness, Tone)
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow |
| **Status** | 🔲 Not started |
| **Active** | Jun |
| **Description** | Formal, versioned scoring rubrics per output type. Each rubric defines dimensions (accuracy 0-100, completeness 0-100, tone 0-100), dimension weights, and pass/fail thresholds. Rubrics are stored as versioned schemas in StateStore, reviewable and modifiable by domain experts. |
| **Impact if not done** | Evaluation is ad-hoc and inconsistent — "good enough" means different things to different users. Without standard criteria, the judge agent has no objective basis for scoring. |
| **Sample tools** | `ScoringRubric(version="1.0", dimensions=[Dimension(name="accuracy", weight=0.5), ...], pass_threshold=0.8)` → stored in StateStore `SupportsEvalRubrics` capability. Editor API for CRUD operations. |
| **Other tasks** | QE-001 (Judge agent needs scoring criteria to evaluate), QE-005 (Domain experts validate criteria), QE-011 (Quality templates ship pre-built rubrics) |

**Jira:** PF-028 (Define Scoring Criteria) — 16h — Jun 8–9

| Subtask | Hours | Date |
|---|---|---|
| Scoring rubric schema + StateStore capability protocol | 8h | Jun 8 (Mon) |
| Rubric CRUD API + versioning | 8h | Jun 9 (Tue) |

---

#### QE-003: Store Evaluation History for Analysis
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow |
| **Status** | 🔲 Not started |
| **Active** | Jun–Aug |
| **Description** | Every eval score, judge rationale, retry count, and final outcome is persisted in StateStore as structured `EvalRecord` events. Queryable by agent_id, use_case, date_range, score threshold. Enables trend analysis, regression detection, and dataset export. |
| **Impact if not done** | You can't track quality trends, identify regressions, or prove improvement over time. Quality is a snapshot, not a story. |
| **Sample tools** | `EvalStore.save(agent_id, rubric_version, scores, rationale, outcome)` + `EvalStore.query(agent_id="*", date_range=..., min_score=0.7)` + `EvalStore.export(format="jsonl")`. Integrates with QE-009's dataset generation pipeline. |
| **Other tasks** | QE-009 (Auto dataset generation feeds from eval history), QE-012 (Quality dashboards query eval history for charts), PF-005 (StateStore durability patterns) |

**Jira:** PF-029 (Store Evaluation History) — 24h — Jun 10–12

| Subtask | Hours | Date |
|---|---|---|
| EvalRecord schema + StateStore adapter + save/query API | 12h | Jun 10–11 (Wed–Thu) |
| Query filters (agent, use case, date, score) + JSONL export | 12h | Jun 12, 15 (Fri, Mon) |

---

#### QE-004: Track What Works and What Fails (Tools, Decisions)
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow |
| **Status** | 🔲 Not started |
| **Active** | Jul–Aug |
| **Description** | Per-tool success/failure rates, decision-path analysis, and common failure pattern mining. Each tool call records outcome (success/error/timeout), latency, and the reasoning context. Aggregated into observability dashboards. |
| **Impact if not done** | You don't know which tools or reasoning patterns cause failures — improvements are guesswork. Buggy tools hide in plain sight. |
| **Sample tools** | `ToolTelemetry(tool_name, success, latency, error_type, decision_path)` published as FlowEvents. A "Tool Health" dashboard view showing top-10 failing tools, failure distribution, and trend lines. |
| **Other tasks** | QE-012 (Quality dashboards surface tool health metrics), PF-009 (A2A observability shares event infrastructure) |

---

#### QE-005: Domain Experts Validate Quality Criteria
| Field | Value |
|---|---|
| **Domain** | 🏗️ Platform |
| **Status** | 🔲 Not started |
| **Active** | Aug |
| **Description** | Workflow for subject matter experts to review, approve, or modify scoring rubrics per domain (finance, healthcare, engineering, legal). Includes diff view, change requests, sign-off, and audit trail. |
| **Impact if not done** | Scoring criteria lack domain authority — agents may pass generic checks but fail domain-specific needs. A finance rubric without a CFO's input will miss critical nuances. |
| **Sample tools** | "Rubric Editor" UI with review workflow: `RubricReview.submit(domain="finance")` → `RubricReview.approve(expert="cfo@corp.com")` → `RubricReview.activate()`. Audit log of who changed what and when. |
| **Other tasks** | QE-002 (Scoring criteria are what experts validate), QE-011 (Quality templates are pre-validated starting points) |

---

#### QE-006: Categorize Questions by Complexity (Easy, Medium, Hard)
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow |
| **Status** | 🔲 Not started |
| **Active** | Aug–Oct |
| **Description** | Incoming queries are classified into complexity tiers (Easy / Medium / Hard) using a lightweight classifier. Taxonomy is data-driven — based on historical eval scores, token usage, and retry counts. Adjustable per use case. |
| **Impact if not done** | Simple queries waste expensive reasoning and hard queries get shallow answers — bad cost/quality tradeoff. You pay premium for everything. |
| **Sample tools** | `ComplexityClassifier.fit(history=EvalHistory)` → `ComplexityClassifier.predict(query)` → returns `"easy" | "medium" | "hard"`. Integrated into `ReactPlanner.run()` as `complexity_tier` context. Tunable thresholds per use case. |
| **Other tasks** | QE-008 (Cheaper models for easy cases depends on complexity classification), QE-011 (Templates ship complexity presets) |

---

#### QE-007: Escalate Disagreements to Human Review
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow |
| **Status** | 🔲 Not started |
| **Active** | Sep |
| **Description** | When judge agent and primary agent disagree on quality (score gap > threshold), or confidence is below minimum, the system pauses and surfaces both outputs to a human for final decision. The human can pick one, merge, or request a new attempt. |
| **Impact if not done** | The system makes final quality calls autonomously — high-risk for regulated or client-facing outputs. A bad output reaches the user with no human safety net. |
| **Sample tools** | Extends PF-003 (HITL Approval) with `DisagreementEscalation(judge_score=0.6, agent_confidence=0.9, gap_threshold=0.3)`. A UI client shows side-by-side comparison with "Accept A", "Accept B", "Modify", "Retry" buttons. |
| **Other tasks** | PF-003 (HITL approval infrastructure is the foundation), PF-012 (Multi-agent HITL shares escalation patterns) |

---

#### QE-008: Use Cheaper Models for Obvious Cases, Expensive for Hard Ones
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow |
| **Status** | 🔲 Not started |
| **Active** | Aug–Oct |
| **Description** | LLM routing tier based on complexity classification: Easy → cheap/fast model (e.g., Gemini Flash, GPT-4o-mini), Medium → mid-tier (e.g., GPT-4o, Claude Sonnet), Hard → capable model (e.g., Claude Opus, o3). Routing decision is transparent and overrideable. |
| **Impact if not done** | You pay premium for every query — costs 3-10x more than necessary without quality benefit for simple cases. At scale, this is the difference between profitable and money-losing. |
| **Sample tools** | `LLMRouter(rules=[(Complexity.EASY, "gpt-4o-mini"), (Complexity.MEDIUM, "gpt-4o"), (Complexity.HARD, "claude-opus-4")])`. Integrates with `penguiflow/llm/routing.py`. Supports per-provider failover within each tier. |
| **Other tasks** | QE-006 (Complexity classification is the input to routing), RFC ideas backlog (Provider failover + model fallback chains) |

---

#### QE-009: Automatic Dataset Generation for Prompt Optimization
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow |
| **Status** | ✅ Done |
| **Active** | Jan–Apr |
| **Description** | Extract trajectory traces from production runs, score them using QE-001 judge, auto-generate labeled datasets for prompt tuning. Supports `docs/proposals/RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md`. |
| **Impact if not done** | Prompt optimization depends on manually curated examples — slow, biased, doesn't scale to hundreds of agent types. |
| **Sample tools** | `DatasetGenerator.extract(state_store, agent_id, date_range)` → `DatasetGenerator.score(judge, dataset)` → `DatasetGenerator.export(format="jsonl", split="train/test")`. Aligns with Phase 1 of the Trace-Derived Datasets RFC. |
| **Other tasks** | QE-010 (Prompt Optimization consumes generated datasets), RFC_TRACE_DERIVED_DATASETS_AND_EVALS (Phases 2-3 for harness evals + optimization templates) |

---

#### QE-010: Prompt Optimization (Improve Agent Instructions Automatically)
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow |
| **Status** | 🔲 Not started |
| **Active** | Apr–Aug |
| **Description** | Automated prompt refinement pipeline: (1) extract dataset from eval history, (2) propose prompt variation, (3) run against test split, (4) compare scores to baseline, (5) deploy if improved. Versioned prompt history with rollback. |
| **Impact if not done** | Prompt engineering is manual trial and error — fragile, time-consuming, and person-dependent. When the person leaves, the prompt knowledge leaves with them. |
| **Sample tools** | `PromptOptimizer(agent_id, dataset_view, metric="accuracy", strategy="dspy_mipro")` → produces `PatchBundleV1` with optimized prompt + score delta. Integrates with DSPy GEPA-compatible templates from RFC Phase 3. |
| **Other tasks** | QE-009 (Dataset generation feeds the optimizer), RFC_TRACE_DERIVED_DATASETS_AND_EVALS Phase 2-3 (Patch bundles + DSPy templates) |

**Jira:** PF-030 (Prompt Optimization Pipeline) — 32h — Jun 16–19, 22

| Subtask | Hours | Date |
|---|---|---|
| Prompt variation proposal + eval harness (run dataset → score → compare) | 16h | Jun 16–17, 18 (Tue–Thu) |
| Deploy-if-improved workflow + versioned prompt history | 16h | Jun 22–23, 24 (Mon–Wed) |

---

#### QE-011: Pre-built Quality Templates by Use Case
| Field | Value |
|---|---|
| **Domain** | 🏗️ Platform |
| **Status** | 🔲 Not started |
| **Active** | Jul–Oct |
| **Description** | Ready-to-use eval templates per domain: reports, analysis, code review, summarization, customer response, data extraction. Each includes scoring criteria, complexity presets, judge prompts, and pass thresholds. Shipped as skill packs. |
| **Impact if not done** | Every team builds evaluation from scratch — inconsistent, slow, and missing edge cases. Adoption of the quality framework stalls because of upfront effort. |
| **Sample tools** | `QualityTemplate.use_case("financial_report")` → loads rubric, judge prompt, complexity taxonomy, and thresholds. Ships as `penguiflow-quality-financial` skill pack. Users customize via a "Templates" gallery. |
| **Other tasks** | QE-002 (Scoring criteria are the core of each template), QE-006 (Complexity presets per template) |

---

#### QE-012: Quality Dashboards (Pass Rates, Common Failures)
| Field | Value |
|---|---|
| **Domain** | 🏗️ Platform |
| **Status** | 🔲 Not started |
| **Active** | Oct–Dec |
| **Description** | Visual analytics: pass/fail rates over time, failure distribution by category, model comparison, cost-per-quality-point, tool health rankings, regression alerts. Filterable by agent, use case, date range, model. |
| **Impact if not done** | Quality is invisible — you can't demonstrate improvement to stakeholders, catch regressions early, or make data-driven decisions about model selection. |
| **Sample tools** | Dashboard "Quality" tab: line charts of pass rate over time, stacked bar of failure categories, heatmap of model×complexity pass rates, tool failure leaderboard. Exportable to PNG/CSV. |
| **Other tasks** | QE-003 (Eval history is the data source), QE-004 (Tool health feeds into dashboards), PF-009 (Observability infrastructure) |

---

#### QE-013: Agents Generation/Optimization MCP
| Field | Value |
|---|---|
| **Domain** | 🏗️ Platform |
| **Status** | 🔲 Not started |
| **Active** | Jul–Dec |
| **Description** | MCP server that exposes agent generation and optimization as tools. Agents can create, evaluate, and improve other agents programmatically via MCP protocol. Enables self-improving agent ecosystems. |
| **Impact if not done** | Agent creation and tuning are manual dev tasks — can't scale to hundreds of specialized agents. Each new use case requires a developer. |
| **Sample tools** | MCP tools: `agent_generate(use_case, rubric_id, complexity_presets)` → creates agent spec, `agent_optimize(agent_id, dataset_view, metric)` → runs prompt optimization, `agent_evaluate(agent_id, dataset_view)` → returns quality report. Exposed as MCP `tools/` endpoint. |
| **Other tasks** | QE-010 (Prompt Optimization is the core engine), QE-009 (Dataset generation provides training data), QE-011 (Templates provide starting points) |

---

#### QE-014: System Learns Quality Standards Per Use Case
| Field | Value |
|---|---|
| **Domain** | 🏗️ Platform |
| **Status** | 🔲 Not started |
| **Active** | Oct–Dec |
| **Description** | ML-based learning: system observes eval outcomes and human review decisions, builds a model of "good enough" per use case, auto-adjusts scoring thresholds and complexity classifications. Thresholds become dynamic, not static. |
| **Impact if not done** | Quality thresholds are static — they don't adapt to evolving user expectations or domain shifts. What was "good enough" in January may be unacceptable by June. |
| **Sample tools** | `QualityLearner.fit(eval_history, human_reviews)` → adjusts `ScoringRubric.pass_threshold` per use case. `ComplexityClassifier.recalibrate()` updates complexity boundaries based on observed cost/quality tradeoffs. Learning is async, human-overridable, and auditable. |
| **Other tasks** | QE-003 (Eval history is training data), QE-005 (Domain expert validation provides ground truth), QE-007 (Human review decisions are training labels) |

---

### Jira Summary — Quality & Evaluation (PF-028 to PF-030 for June)

| Ticket | Theme | Active | Estimate | Status |
|---|---|---|---|---|
| PF-028 | QE-002: Define Scoring Criteria | Jun 8–9 | 16h | 🔲 |
| PF-029 | QE-003: Store Evaluation History | Jun 10–12, 15 | 24h | 🔲 |
| PF-030 | QE-010: Prompt Optimization Pipeline | Jun 16–19, 22 | 32h | 🔲 |

June total for Q&E: 72h — fits within ~160h June capacity alongside existing PF-016 through PF-023.

---

*This document is a living artifact. Update it as features ship, priorities shift, or new tickets are created.*
