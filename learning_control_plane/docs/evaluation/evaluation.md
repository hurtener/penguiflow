# Standalone local evaluation

`LocalEvaluationBackend` evaluates the same fixed `EvaluationDataset` twice:

1. the baseline `EvaluationVariant`, which has no advisory skill;
2. the candidate `EvaluationVariant`, which has one advisory skill.

The caller provides two small functions:

```python
async def run_one(case, variant):
    return await agent.run(case.inputs, advisory_skill=variant.advisory_skill)

def score(case, output):
    return {"quality_score": 1.0 if output == case.expected else 0.0}
```

The backend creates a result for every case and variant. A runner or metric failure
is captured as a `VariantCaseResult.error`; it is never silently removed from the
comparison. `EvaluationDataset.manifest_digest` pins the exact JSON-serializable
cases that were used.

When given an `EvidenceSink`, the backend emits `evaluation.started` and
`evaluation.completed` receipts containing only the dataset digest, variant IDs,
case/failure counts, and aggregate scores. It does not export case inputs or agent
outputs.
