# Offline learning decision workflow

`LearningControlPlane` manages only advisory-skill candidates in the MVP. It has no
method to change a live agent, tool policy, permissions, or code.

```text
register candidate → create draft job → run offline evaluation
                                        ↓
                               deterministic gate
                              ↙                    ↘
                         rejected           ready_for_review
```

The caller invokes `run_job()` from an external scheduler or worker. It evaluates a
fixed dataset with the existing baseline and one advisory-skill candidate. The gate
requires complete baseline/candidate pairs, a configured primary-metric improvement,
and no regression in configured protected metrics.

An evaluator infrastructure error moves a job to `failed`; it creates no decision.
`retry_job()` is the only retry path and it is limited by `PromotionPolicy`.
Evaluation case failures stay in the evaluation result and cause the gate to fail
closed by default (`maximum_failed_cases=0`).

An approved reviewer decision enables a scope-bound, expiring delivery authorization.
The host/provider applies it and sends an `ActivationReceipt` back to the control
plane. The control plane accepts only matching, active receipts and can revoke an
authorization. It still never changes an agent itself.
