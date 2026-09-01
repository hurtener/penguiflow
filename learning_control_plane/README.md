# Learning Control Plane

The Learning Control Plane (LCP) turns evidence from production agent usage into
small, governed runtime assets. Its first asset type is an **advisory skill**: text
the agent may use or ignore. It never changes agent code, permissions, tool policy,
or the live request path.

The LCP is intentionally framework-neutral. PenguiFlow is the first planned
provider, not a dependency of the control-plane core.

## MVP boundary

The MVP is an offline workflow:

1. Read immutable trace and outcome evidence.
2. Create a candidate advisory skill.
3. Run baseline and candidate versions against the same held-out cases.
4. Apply deterministic improvement and regression gates.
5. Send a passing candidate to a human reviewer.
6. Authorize scoped delivery and record an activation receipt.

If the LCP, its scheduler, or its evidence store is unavailable, production agents
continue to serve requests with their last valid configuration.

## Package map

| Path | Responsibility |
|---|---|
| `evidence.py` | The current Milestone 1 evidence records and optional telemetry sinks. |
| `investigation.py` | Portable investigation document, canonical JSON bytes, digest, and discovery index. |
| `investigation_trajectory.md` | InvestigationTrajectoryV1 contract and canonicalization rules. |
| `investigation_publisher.py` | Idempotent MLflow trace-attachment publisher for investigation documents. |
| `mlflow_lineage.md` | MLflow tag, metric, and artifact-path convention. |
| `evaluation.py` | Standalone baseline-versus-advisory-skill local evaluator. |
| `evaluation.md` | The evaluator's fixed-data and complete-evidence contract. |
| `control_plane.py` | Offline candidate registry, job lifecycle, and deterministic gate. |
| `control_plane.md` | The MVP decision workflow and its safety boundary. |
| `architecture.md` | Boundaries and evidence flow for the MVP. |
| `MILESTONES.md` | Ordered delivery plan and completion criteria. |

Future code folders are intentionally not scaffolded yet. They will be created when
their milestone introduces a real evaluation backend, control-plane workflow, or
PenguiFlow provider.

See [the milestones](MILESTONES.md) before adding production behavior.
