# MLflow lineage convention, v1

This convention makes one MLflow evidence record traceable to the exact agent,
candidate, evaluation, and policy that produced it. It is evidence only: MLflow
does not make promotion or delivery decisions.

## Tags

Every record carries these tags:

```text
lcp.lineage_schema = v1
lcp.event_id
lcp.event_type
lcp.occurred_at
lcp.agent_id
lcp.deployment_digest
```

When known, it also carries:

```text
lcp.trace_id
lcp.evaluation_id
lcp.candidate_id
lcp.dataset_version
lcp.metric_version
lcp.policy_version
lcp.scope_ref
```

`trace_id` refers to the source agent trace. It is a pointer to the
access-controlled trace store, not a copy of the trace contents.

## Metrics

Numeric evaluation results are recorded with the `lcp.metric.` prefix. For example:

```text
lcp.metric.quality_score = 0.91
lcp.metric.regression_rate = 0.00
```

Metrics must be finite numeric values. Counts and scores belong in `metrics`; other
metadata belongs in `attributes`.

## Evidence receipt

The complete redacted record is stored as a JSON artifact at:

```text
learning_control_plane/evidence/v1/<event-id>.json
```

The receipt includes the same identity fields, its metrics, and safe metadata. It
must not include prompts, message content, tool inputs/outputs, credentials, or
tokens.

## Recommended event names

Use a verb in the past tense and a domain prefix:

```text
trace.recorded
evaluation.started
evaluation.completed
evaluation.failed
candidate.created
gate.decided
review.decided
delivery.authorized
delivery.receipted
delivery.revoked
```

Later milestones will emit these events; v1 reserves their names now so MLflow
queries and dashboards do not need to guess between spellings.
