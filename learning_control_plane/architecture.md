# MVP architecture

## Boundary

The control plane is an offline decision service. It consumes evidence and sends
authorized delivery requests; it never sits on the inference path.

```text
Production agent ──► OpenTelemetry / MLflow evidence store
                                      │
                              scheduled offline job
                                      │
                 Learning Control Plane core
        candidate → evaluation → gate → human review → authorization
                                      │
                              framework provider
                                      │
                         scoped activation + receipt
```

MLflow stores traces, datasets, scores, artifacts, and lineage. It does not decide
whether an asset should be promoted. The control plane owns that decision through
versioned policy and recorded human approval.

## MVP invariants

- The only candidate asset is an advisory skill.
- A candidate cannot grant permissions, force a tool call, run code, or alter a
  flow graph.
- Baseline and candidate use the same immutable agent bundle, model, tool catalog,
  evaluation data, and metric version.
- The promotion cohort is held out from candidate selection.
- Every decision records candidate, evidence, policy, scope, reviewer, and result.
- Delivery is scope-specific and must return a receipt. Invalid, expired, or
  revoked authorization must be rejected by the provider.
- The LCP is allowed to fail closed: it may decline to deliver a candidate, but it
  must not make a running agent unavailable.

## Integration split

`evaluation/` is usable independently by any agent application. A framework
provider is optional and supplies framework-specific trace projection, candidate
compilation, and activation. The PenguiFlow integration will use existing
trajectories and skills without requiring the planner to call the LCP synchronously.
