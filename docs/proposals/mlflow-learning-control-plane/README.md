# MLflow-Backed Learning Control Plane

*Executive architecture brief — August 2026.*

**In one sentence:** after initial platform integration, the Learning Control Plane helps production agents improve from real usage through governed runtime assets, without repeated core-agent releases or exposing customers to unproven changes.

**Decision:** use MLflow for projected datasets, evaluations, scorers, assessments, prediction traces, and lineage, and a separate Learning Control Plane for authoritative proposal, approval, and delivery records. Runtime evidence authority remains deployment-selectable.

# Start here

You do not need to read every document. Use this guide to find the level of detail you need.

| If you want to understand... | Read | What you will learn |
| --- | --- | --- |
| **What are we building and why?** | **This README** | The problem, proposed service, safety boundaries, benefits, limitations, and rollout plan. Start here. |
| **What does leadership need to know?** | [Executive brief](./executive-brief.md) | A short, non-technical summary of the business value, guarantees, ownership model, and delivery stages. About a five-minute read. |
| **How will the whole system work?** | [Technical architecture](./architecture.md) | The detailed design: components, responsibilities, data contracts, evaluation lifecycle, approvals, security, and delivery. Read this after the README. |
| **How will agents be tested?** | [Evaluation backends](./evaluation-backends.md) | How PenguiFlow runs evaluations locally or with MLflow, what both approaches share, and what each system owns. |
| **Where should production evidence be stored?** | [Runtime evidence architecture decision](./runtime-evidence-architecture-decision.md) | A comparison of MLflow tracing, OpenTelemetry, external evidence storage, an owned evidence ledger, and dual export. Use this when choosing a deployment approach. |
| **What research supports this design?** | [Research evidence](./research-evidence.md) | Relevant papers and systems, ideas worth reusing, known weaknesses, and unanswered questions. This is reference material, not required first reading. |

## Key terms in plain language

- **Learning Control Plane:** a service that finds possible improvements, coordinates testing, records approvals, and controls delivery.
- **Skill:** versioned instructions that may help an agent perform recurring work. A skill cannot add permissions, run code, or force actions.
- **Runtime evidence:** a record of what an agent did during a real run, including tool use, failures, and its result.
- **Evaluation:** a controlled test that compares the unchanged agent with the same agent using a candidate skill.
- **MLflow:** the system that stores evaluation datasets, results, scores, traces, and links between them.
- **Learning Plane provider:** a framework-specific integration that publishes approved evidence and delivers confirmed skills.
- **Control plane:** the governance and coordination layer. It runs outside the live customer request path.

## Diagrams

- [Governed agent evolution](./governed-agent-evolution.png) shows the high-level path from production evidence to a tested, approved, and reversible improvement. It appears later in this README.
- [Governed machine learning loop architecture](./Governed_Machine_Learning_Loop_Architecture.png) shows the systems and people involved in the learning loop. It appears in the [executive brief](./executive-brief.md).

For a run to become learning evidence, two signals are needed:

- **What the agent did** — its trace, tool use, failures, and final result.
- **Whether it went well** — user, business, policy, quality, cost, and latency assessments.

The system connects those signals into one controlled loop:

```mermaid
graph LR
    A[Observe real runs] --> B[Find repeated procedures]
    B --> C[Draft bounded skill]
    C --> D[Prove on later cases]
    D --> E[Human confirmation]
    E --> F[Scoped delivery]
    D -->|Fails gates| X[Reject]
```

This is not an agent that rewrites itself. It learns bounded runtime assets that remain visible, versioned, reversible, and subordinate to existing permissions.

![Governed AI agent evolution process](./governed-agent-evolution.png)

> Start with a sizing experiment, not a full build. If evidence contains too few repeated procedures, or outcomes are too weak to judge them, the learning loop is not worth operating. Standalone MLflow evaluation remains useful either way.

# Architecture

Control-plane discovery, evaluation, approval, and delivery run outside the live request path. Runtime evidence capture is deployment-selectable and never depends synchronously on Learning Control Plane availability. All supported capture paths converge on MLflow-backed datasets, evaluations, scorers, assessments, and lineage. If the plane is unavailable, agents continue with their last confirmed configuration.

The capture choice depends on runtime isolation, operating cost, customer environment, and evidence-governance needs. [Runtime Evidence Architecture Decision](./runtime-evidence-architecture-decision.md) records the trade-offs without selecting one implementation for every deployment.

The following logical view isolates system boundaries and authoritative handoffs:

```mermaid
flowchart TB
    A[Production agent runs]
    R[Selected runtime<br/>evidence capture]
    O[Authorized outcome snapshots]
    M[MLflow evidence]
    C[Control Plane discovers<br/>and proposes skill]
    E[Agent evaluation package<br/>PenguiFlow evaluates locally]
    G[Control Plane verifies evidence<br/>and applies gates]
    H[Authorized human review]
    D[Provider rechecks policy<br/>and delivers confirmed skill]

    A --> R
    R --> M
    O --> M
    M --> C
    C --> E
    E -->|Results stored in MLflow| G
    G --> H
    H --> D
```

| Owner | Responsibility | Explicit boundary |
| --- | --- | --- |
| Agent teams | Run agents and define domain quality and policy | Domain knowledge stays with the team |
| MLflow | Store projected datasets, scores, evaluation evidence, and lineage | Evidence store, not approval authority |
| Learning Control Plane | Select candidates, coordinate evaluation, apply policy, and authorize delivery | Does not become an agent runtime |
| Learning Plane provider | Publish portable evidence and deliver confirmed assets | Cannot bypass live permissions or scope |
| Platform operator | Operate policy, reliability, receipts, incidents, and deactivation | Does not define domain success |

Raw business outcomes remain in domain systems. Only authorized learning snapshots enter MLflow. Canonical raw runtime evidence remains in the store selected by the deployment's capture profile.

Customer isolation is enforced through tenant-isolated resources or authenticated service-mediated access on every read and write, including the selected raw-evidence store and MLflow. Tags and search metadata help locate evidence; they never establish access rights.

# The framework boundary

Each supported framework will expose two separate integrations:

- a **standalone MLflow evaluation backend** for datasets, prediction, scoring, and evaluation runs;
- an optional **Learning Plane provider** that publishes a redacted investigation record, proves candidate use, and delivers confirmed assets.

Teams can adopt MLflow evaluation without adopting continuous learning.

Portable contracts connect supported frameworks without centralizing their native traces, replay behavior, evaluation semantics, or domain judgment. [Technical architecture](./architecture.md) defines those boundaries.

**Design principle:** the contracts carry the product, not PenguiFlow internals. After the first provider is proven, supporting another framework should require an adapter, not a central-service rewrite.

# What agents learn

The first release learns **advisory Agent Skills**: reusable playbooks that say, “for this kind of request, these steps tend to work.” The agent may use or ignore the advice.

```mermaid
graph LR
    S[Now: advisory skills<br/>Human confirmed] --> W[Later, if skill loop is reliable:<br/>workflows]
    W --> O[Later: broader optimization<br/>Conditional automation]
```

A learned skill cannot grant tool access, change authentication, run code, force a tool call, alter core application code, or activate itself. Existing runtime permissions always win.

Deterministic **Workflows** are the next candidate surface if the skill loop proves reliable. They can remove repeated decisions and lower cost, but act more directly and require stronger replay, side-effect, rollout, and rollback controls.

# How improvement is proven

1. **Find on one cohort; judge on another.** Evidence used to discover a skill cannot also prove it works. That would grade the system's own homework.
2. **Test on the future, not the past.** Mine in one time window and validate on a later, frozen window.
3. **Require relevant coverage.** Every skill proposal identifies evaluation cases representing its claimed improvement. When coverage is missing, the agent-owned evaluation package proposes cases from authorized evidence and the domain owner admits them before promotion.
4. **Do not self-validate.** A case derived from skill-discovery evidence may prove reproduction and remediation, but not generalization; independent later cases remain required.
5. **Change one thing.** Baseline and candidate use the same agent, model, tools, configuration, and cases. The intended difference is the exact skill.
6. **Prove the skill was used.** A candidate that was never selected, changes no case, or improves no case is rejected.
7. **Fail closed.** Missing results, scorer failures, mismatched cases, or blocking regressions stop promotion.
8. **Keep judgment with the domain.** Agent teams own business scorers and evaluation-case admission. Probabilistic MLflow judges may assist but are not the sole promotion authority.
9. **Bind every decision to the exact asset.** Evaluation, confirmation, and delivery reference the same immutable skill digest. MLflow review is evidence only; the Learning Control Plane authenticates the reviewer, verifies their role, and records the authoritative decision.
10. **Verify the handoff.** Before delivery, the provider rechecks live target policy and customer scope, installs the exact confirmed version, and records a scope-bound receipt.
11. **Keep the off-switch.** Every delivered skill can be deactivated by the platform operator with a receipt. Off-switch behavior must be selected and tested before delivery exits. If evidence cannot be verified, learning stops while production agents keep running.

Investigation records contain only allowlisted or redacted content. Native evaluation datasets have a separate authorization, redaction, and secret-check boundary. Trace text, tool output, feedback, generated skills, and scorer claims are treated as untrusted; blocking scorer identity and version are verified independently.

External learning execution must prove isolation, bounded access, and controlled side effects before production use.

# Benefits and limitations

| Benefits | Limitations |
| --- | --- |
| One reusable evidence and governance loop | Human confirmation slows promotion |
| Improvements without core agent code changes | Agent teams still maintain domain scorers |
| Domain knowledge remains with agent teams | Sparse or weak outcomes limit learning value |
| Framework choice remains open | Safe evaluation may require isolated or read-only tools |
| Full trail from source run to delivered skill | MLflow capabilities vary by deployment profile |
| Learning failures do not interrupt production | First release learns advisory skills only |
| Trace-derived cases preserve production evidence | Novel behavior remains unproven until a domain-reviewed case represents it |

The design favors controlled improvement over autonomy. It does not initially optimize prompts, parameters, routing, permissions, code, or executable workflows.

# Current position

MLflow API-path feasibility has been demonstrated. An enterprise-agent experiment completed this path:

```text
Source trace → MLflow dataset → Agent evaluation → Custom scorer → Linked evidence
```

PenguiFlow is the first planned framework integration. Its native trajectory and planner-event models provide foundations, but its target Learning Plane APIs are not yet implemented.

**Important limit:** this proves the MLflow API path, not the complete learning product. Phase 0 must still prove the selected MLflow profile and version, PenguiFlow execution path, portable investigation publication, separate redaction boundaries, and failure handling.

# Roadmap

```mermaid
graph LR
    P0[0. Harden feasibility<br/>Choose profile and size value] --> P1[1. Evaluation<br/>Ship standalone MLflow path]
    P1 --> P2[2. Read-only pilot<br/>Prove one skill]
    P2 --> P3[3. Governed delivery<br/>Confirm, deliver, deactivate]
    P3 --> P4[4. Expand if proven<br/>Workflows or second framework]
```

| Phase | Exit outcome |
| --- | --- |
| Harden feasibility | Supported MLflow profile, executor and cost model, redaction boundary, and preregistered go/no-go gates |
| Standalone evaluation | Complete trace-to-dataset-to-score lineage without Learning Plane dependency |
| Read-only pilot | One skill discovered and fairly evaluated, with review evidence but no delivery |
| Governed delivery | One confirmed skill delivered to one bounded scope with receipt and off-switch |
| Expand | Workflows or another framework considered only after the skill loop is reliable |

Each completed phase produces useful output; later phases depend on prior exit criteria. Even if the learning pilot stops, standalone evaluation and reusable labelled datasets retain value.

# Decisions required

Before production work begins, leadership must choose:

1. **Deployment:** OSS MLflow with SQL storage or Databricks, plus one authoritative skill-package store.
2. **Runtime evidence:** capture profile and authoritative raw-evidence store, using the linked decision criteria.
3. **Pilot:** first agent, domain, and customer scope with enough trusted outcomes.
4. **Ownership:** domain scorer and approver teams, plus platform policy, operations, and incident owners.
5. **Data policy:** retention, deletion, regional, and customer-content rules.
6. **Operating model:** evaluation compute, model-judge spend, human-review capacity, and service support.
7. **Go/no-go threshold:** preregistered numeric outcome coverage, recurrence and sample floors, minimum improvement, no-regression gates, review capacity, and stop condition.

The pilot succeeds when one recurring procedure becomes one measurably better, human-confirmed, scope-bound skill with a complete audit trail and a proven off-switch — without creating a dependency in the live customer request path.
