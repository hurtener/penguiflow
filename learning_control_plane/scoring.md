# Direction-aware scoring

Each scorer still returns a mapping of named numeric outcomes for one case. For
example, one scorer can return task success, policy compliance, latency, cost,
and tool-error rate together.

`MetricSpecification` declares the direction for every metric used by a
promotion policy:

- `higher_is_better` is the default for scores such as task success and policy
  compliance.
- `lower_is_better` is for outcomes such as latency, cost, customer correction
  rate, and tool-error rate.

For every complete baseline/candidate pair, `MetricSummary` keeps both raw values
and a direction-normalized improvement. A positive improvement always means the
candidate is better:

```text
higher_is_better: candidate - baseline
lower_is_better:  baseline - candidate
```

The summary exposes mean, median, minimum, and maximum paired improvements.
`GateDecision` stores these paired summaries and the normalized mean improvement
alongside the existing baseline and candidate means. A primary metric must meet
its configured minimum improvement; every protected metric must remain at or above
zero normalized improvement.

This is descriptive evidence for the deterministic MVP gate, not yet a confidence
claim. Statistical confidence intervals and sample-size rules remain the next
milestone.
