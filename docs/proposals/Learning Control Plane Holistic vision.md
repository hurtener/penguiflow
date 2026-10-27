# Learning Control Plane

*Working documentation — draft v0.2.*

**In one sentence:** the Learning Control Plane is an offline nightly system that helps production agents improve from real usage without code releases and without exposing customers to unproven changes.

**What it is:** production agents that get measurably better over time by learning from real usage — with no code release, and never running an unproven change against a customer. It runs as a separate **offline (nightly) service**; nothing in the live request path changes except turning on improvements that already passed validation.

For every run, two signals already exist:

- **What the agent did** — its step-by-step history in **StateStore**.
- **Whether it went well** — the user's reaction in **Iceberg**.

Today those recordings mostly sit unused. The Learning Control Plane turns them into a controlled improvement loop:

- **joins** execution and outcome into one labelled record;
- **finds** patterns in what worked;
- **proves** each candidate beats today’s agent on real past runs;
- **rolls it out** gradually, scoped to one customer and reversible.

> This capability already exists manually — a developer can flip a flag to skip a repeated step, or hand-write a playbook. What does not exist is doing it at scale. So the first step is a cheap sizing query, not a build. If the data shows few repeated patterns worth capturing, the system is not worth much.

# Architecture

The plane is a scheduled batch job aligned with the feedback store’s nightly consolidation. Most of it lives in a separate service. The agent platform grows only a small, bounded set of new pieces. 

# The two signals it feeds on

- **Execution truth** — the step-by-step history of what the agent did. Lives in **StateStore**; one run is a *trajectory* or *trace*. Think of this as the security-camera footage. 
- **Outcome truth** — whether the user was happy, inferred from corrections, thumbs up/down, re-asks, exports, or contested facts. Lives in **Iceberg**. Think of this as the customer comment card.

These line up one-to-one because each feedback row carries the same run ID used by the trace. That match is **the join**, and it is driven from the Iceberg side, where customer identity (*tenant*) authoritatively lives.

# The pipeline

| Stage | What it does |
| --- | --- |
| Signal spine | Incrementally joins new traces with outcomes, per customer and time window |
| Label compiler | Turns feedback into good, bad, mixed, or unknown; enforces the leakage firewall |
| Miners | One finds reusable playbooks (*skills*), one finds deterministic shortcuts (*edges*) |
| Validation gate | Tests each candidate against today’s agent on a later, unseen window |
| Activation | Uses a light path for skills and a gradual ladder for shortcuts |
| Governance | Maintains decision ledger, approvals, timers, rollback, and kill-switches |
| Observability | Measures the learner itself |

# The service-to-core boundary

Most of the pipeline stays in the separate control-plane service. The agent platform, PenguiFlow, grows only a bounded set of pieces:

- shared contract types;
- a learned-skill write path;
- planner machinery to apply a learned shortcut;
- a saved snapshot of each run’s customer scope;
- the global kill-switch.

The design principle is that the **contracts** carry the product, not PenguiFlow internals. That keeps a second framework in the future as an adapter project rather than a rewrite. Core anchors called out in the RFC include `react_runtime.py`, `skills/local_store.py`, and `planner/patch_points.py`. 

# What the agents actually learn

The system learns **runtime assets only**. It does **not** learn core code, tool permissions, auth scopes, or flow structure. In v1 it learns exactly two things, with different handling because they carry different risk. *0*

| Type | What it is | Effect on the agent | Risk | Promotion path |
| --- | --- | --- | --- | --- |
| Learned skill | A reusable playbook: “for this request, these steps tend to work” | Advice the agent can use or ignore | Lower blast radius because it is advisory | Draft → Active |
| Learned shortcut (edge) | A repeatable tool-to-tool step taken without re-deciding each time | Removes a thinking step, making execution faster, cheaper, and more consistent | Higher blast radius because it acts directly; read-only by default and write requires human sign-off | Draft → Shadow → Canary → Active |

# How the loop works

1. **Join and label.** Pull new runs by customer and time window, match each to its outcome, and compile a label. A run with *no* feedback is `unknown`, never “good.” Absence of an error is not success. 
2. **Do not grade your own homework.** The user-feedback signal is used to *find* candidates, so it must not also *judge* them. Skills are judged on goal completion against a frozen human-curated gold set. Shortcuts are judged on reproducing a known transition plus a sample outcome check. The judge uses a different model family than the generator, and gold used in validation is firewalled out of mining. 
3. **Test on the future, not the past.** Mine on window W and validate on a strictly later window W+1. This tests generalization rather than rediscovery.
4. **No lucky-shot promotions.** Promotion requires the pessimistic end of a bootstrap confidence interval on score delta to clear a minimum effect size, on enough samples, plus a deterministic safety floor that can veto regardless of score. 
5. **Different risk, different ladder.** A skill that passes goes straight to active as advice. A shortcut moves through **shadow** (loaded but never fires), then **canary** (fires for a small scoped slice), then **active**.
6. **Keep a permanent control.** Even at full rollout, about 5% of the time the shortcut is ignored and the model decides the old way. This ε-exploration control helps detect drift, supports rollback against concurrent reality, and keeps learned skills competing. *(RFC §5.7, §16)*
7. **Safety comes from layered off-switches.** Precedence runs from global kill-switch to operator master switches to learned-shortcut knob to per-asset state to per-firing checks. Nothing learned overrides a human master switch. Everything is reversible, expires on a timer, and stays inside one customer boundary. No cross-customer pooling, no raw content, no secrets, and no literal arguments are stored. 

# What exists already

Foundation is described as roughly 70% in place; the net-new work is the loop that connects the pieces.

**Already in production:**

- **Execution truth** — StateStore trajectory and planner-event history. 
- **Outcome truth** — Iceberg feedback, corrections, contested facts, and preference signals.
- **The join key** — Iceberg tags each feedback row 1:1 with its run. 
- **Skill injection** — the platform can already format a playbook into planner context with `format_for_injection`.
- **Shortcut detection scaffolding** — basic types and events for spotting deterministic transitions.
- **The manual capability** — a developer can already flip the shortcut flag or hand-write a skill; the gap is automation and scale.

**The evaluation substrate (PR #115)** is the testing bench the plane is built on. It turns recorded runs into datasets, runs metrics, and compares baseline versus candidate. It includes trace export, dataset rows and manifests, metric execution, manual sweeps, and CLI or Playground tooling. 

**Important limit:** PR #115 is not yet a finished, framework-neutral learning plane. It is PenguiFlow-shaped, relies heavily on loose dictionaries rather than hardened schema, records redaction alongside exports rather than enforcing it as a boundary, uses thresholded plain scores instead of the full statistical gate, and can currently no-op while still producing a score unless evaluations are designed to fail closed.

# What is currently being built

**PR #115 — the evaluation substrate — is the active work in flight.** It is open, includes some conflicting or dirty-state work, and has no reported passing checks, so it needs rebase and CI acceptance before landing. That landing is intentionally kept separate from the Learning Plane scope: merging the bench is one job; building the loop on top of it is another. The stated plan is to land the bench first, then add substrate-level pieces that are still missing, especially windowed trace enumeration and dataset-to-metric coupling.

# What still needs to be built

Everything that turns the substrate into a full learning loop is still ahead, grouped below. *(RFC §8)*

| Area | Needed work |
| --- | --- |
| Ingestion and joining | Signal spine, incremental join, watermarking, label compiler, leakage firewall, tenancy reconciliation, saved scope snapshot per run, and windowed trace enumeration by customer and time |
| Evaluation | Out-of-time dataset builder, dataset-to-metric coupling, statistical confidence-interval gating, and reference-guided paired judging for skills |
| Skill activation | Learned-skill write path, scope-aware read and dedup, and store migration from globally unique names to scoped identity |
| Shortcut activation | Patch-point registry, planner application, typed field remapping, tool-author opt-out marker, ambiguity scan, side-effect-class pinning, chain-depth cap, shadow/canary rollout, ε-exploration, and propensity logging |
| Governance and observability | Append-only promotion ledger, global kill-switch, and learner metrics such as promotion precision, rollback rate, traffic served, and never-fired rate |

# Recommended sequencing

1. **Land the bench (PR #115).**
2. **Run join and labels as a feasibility experiment** with pre-registered go/no-go criteria and no asset generation.
3. **Ship the read-only shortcut track first.**
4. **Add the skill track** once gold coverage clears its floor.

Each phase is independently shippable. Even phase one produces reusable labelled datasets, which have value whether or not the rest of the system ships.

Shortcuts have abundant self-generating evidence because they can be judged against known transitions and narrow safety checks. Skills need curated gold and a stronger judging setup, so they are gated by human evaluation capacity as much as by engineering readiness.
