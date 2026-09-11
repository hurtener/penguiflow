# MLflow investigation mining

`MlflowInvestigationReader` is the read-only bridge from published MLflow
investigation attachments to `TraceLearningRecord` objects.

It discovers only investigations tagged `completed`, optionally filters by agent,
provider, and scope, and then verifies every document before it can enter mining:

1. Read the MLflow attachment reference from the publisher's trace output.
2. Download the referenced attachment from that trace's artifact repository.
3. Parse `InvestigationTrajectoryV1` and require its exact canonical bytes.
4. Recompute its SHA-256 digest and compare the full MLflow discovery index.

Malformed, non-canonical, or mismatched evidence fails closed. The reader never
silently turns an unverified attachment into a candidate input.

The record handed to `CandidateMiner` includes only the agent/deployment identity,
source time, source trace ID, investigation digest, step signature, and a fixed
summary of counts and booleans. It does not include request text, step payloads,
observations, final output, or attachment bytes.

Use `load_cohorts(..., held_out_count=N)` to optionally reserve the newest
verified records before mining earlier successful records. Pass
`held_out_count=0` when the host has a separately frozen evaluation dataset and
all selected traces are intended as training evidence. To evaluate a reserved
cohort, call `build_held_out_evaluation_cases(records, build_case)`. The host
owns `build_case`: it may retrieve approved real evaluation input through the
native run reference, but raw input does not enter the mining or drafting
boundary.

Try the local end-to-end attachment reading flow with:

```bash
uv run python examples/learning_control_plane_local_demo/mlflow_investigation_mining_demo.py
```
