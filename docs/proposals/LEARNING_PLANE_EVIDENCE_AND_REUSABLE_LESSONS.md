# Learning Plane: Evidence and Reusable Ideas

Executive catalog of external research and implementations relevant to learning
services. It records observed patterns and limitations without deciding whether
or how they will be adopted.

## Recuris

- **Paper:** [Recursive Experiential-Working Memory Evolution for Long-Horizon Agent Harnesses](https://arxiv.org/abs/2608.24876)
- **Repository:** [Gen-Verse/Recuris](https://github.com/Gen-Verse/Recuris)
- **Evidence:** Published benchmark results and reference implementation. Local
  review found substantial validation logic but no automated test suite.

### Core Idea

Keep the agent frozen. Evolve reusable experience from execution trajectories,
then retain changes only when deterministic held-out evaluation improves.

### Observed Patterns

| Pattern | Possible relevance |
|---|---|
| Finalized evidence as authority | Avoids learning from partial runs, discarded retries, or synthetic events. |
| Evidence separated from interpretation | Keeps generated summaries and diagnoses from becoming self-validating facts. |
| Explicit abstention | Allows weak or conflicting evidence to produce no change. |
| Reachability checked before quality | Distinguishes delivery failures from ineffective content. |
| Mechanism-contact fingerprints | Shows whether the evaluated change was present during each execution. |
| Model proposals with deterministic gates | Separates creative candidate generation from admission decisions. |
| Paired held-out comparison | Measures changes on aligned cases and exposes regressions hidden by aggregate scores. |

### Limitations and Open Questions

- Runtime, working-memory, and benchmark harness concerns are tightly coupled.
- Evidence comes from controlled benchmarks rather than tenant-scoped production
  workloads.
- Repository does not provide an automated test suite.
- Applicability of its memory-card taxonomy outside its runtime is unclear.

## WikiSkill

- **Paper:** [WikiSkill: Compiling Agent Experience into Persistent Knowledge for Skill Evolution](https://arxiv.org/abs/2608.27454v1)
- **Repository:** No official repository found.
- **Evidence:** Research preprint covering five benchmarks and five models, with
  three evolution runs and paired bootstrap tests.

### Core Idea

Separate immutable trajectories, persistent derived knowledge, and active
skills. Consolidated patterns inform atomic skill changes that can be evaluated
and rolled back.

### Observed Patterns

| Pattern | Possible relevance |
|---|---|
| Three knowledge layers | Distinguishes source evidence, accumulated interpretation, and active procedural guidance. |
| Persistent pattern consolidation | Lets lessons compound across learning cycles instead of restarting from raw traces. |
| Rejected changes retained | Negative results may prevent repeated ineffective proposals. |
| Successes and failures both mined | Preserves working procedures while identifying recurring failure patterns. |
| Learning context isolated from inference | Paper reports better results when accumulated wiki knowledge informs the proposer but is not exposed directly to the runtime agent. |
| Atomic changes with rollback | Makes outcome attribution and reversal simpler than broad simultaneous mutation. |
| Transfer treated empirically | Cross-model skills can help or harm depending on whether guidance is general or model-specific. |

### Limitations and Open Questions

- No official implementation repository was identified.
- Runtime skill retrieval was bypassed through full-prompt injection.
- Persistent wiki has no pruning mechanism.
- Small validation sets can make immediate acceptance gates noisy.
- Cross-model transfer includes harmful cases and requires target-specific
  validation.
- Evidence comes from benchmarks, not privacy-constrained production systems.

## Signal or Noise?

- **Paper:** [Signal or Noise? A Benchmark Study of Agent Skills in Web Development](https://arxiv.org/abs/2608.23067)
- **Repository:** [Paper-provided analysis artifacts](https://anonymous.4open.science/r/webdev-skills-bench-1C32/)
- **Evidence:** Pre-deployment benchmark of 31 public skills across 117 matched
  skill-project pairs, four models, and three replicates per condition. It
  compares no-skill, matched-skill, length-matched irrelevant-skill, and
  selected component-ablation conditions.

### Core Idea

Injected skills are conditional interventions rather than inherently beneficial
context. Their effects can vary with model, task, content, prompt length, and
position in a sequential workload.

### Observed Patterns

| Pattern | Possible relevance |
|---|---|
| No-skill baseline | Shows whether guidance improves on native model behavior rather than only whether a task succeeds. |
| Length-matched control | Helps distinguish useful content from cost or interference caused by additional context. |
| Context-specific effects | Preserves variation across model, skill, task, difficulty, and chain position that aggregate scores can hide. |
| Positive minority under negative averages | Allows targeted value and broad harm to coexist in the same evaluation result. |
| Weak cross-model transfer | Treats evidence from one model as uncertain evidence for another. |
| Sequential recovery measurement | Captures whether guidance affects retries and progress through dependent tasks. |
| Component-level comparison | Separates effects of positive rules, anti-patterns, and examples. |
| Resource overhead | Includes token cost alongside task-quality measurements. |

### Limitations and Open Questions

- Full `SKILL.md` content was injected; retrieval, ranking, selective loading,
  and dynamic activation were not evaluated.
- Study does not generate, evolve, or promote skills.
- Component ablations cover five preselected positive skill-project pairs.
- Three replicates leave material uncertainty at pair level.
- Functional tests exclude UX, accessibility, maintainability, review effort,
  latency, and production outcomes.
- Agent framework and tool-loop details are not clearly identified.
- Artifact repository omits raw evaluation reports and complete trajectories.
- Results cover public web-development skills and may not transfer to other
  domains or internal skill collections.
- Attention dilution and retry lock-in are interpretations rather than directly
  measured mechanisms.

## Catalog Entry Template

```text
## Source
Paper:
Repository:
Evidence:

### Core Idea

### Observed Patterns
| Pattern | Possible relevance |

### Limitations and Open Questions
```
