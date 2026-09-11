# Verified learning evidence

Completion is not proof that an agent was correct. Candidate mining therefore
requires a `learning_verification.v1` record in addition to a completed
`InvestigationTrajectoryV1`.

## E0–E6 implementation

- **E0 — Safe evidence contract:** `VerificationCheck`, `SafeStepEvidence`,
  `InvestigationVerification`, and the final-answer rubric are typed and
  versioned. Safe argument facts accept identifiers, counts, booleans, and finite
  numbers; free-form text is rejected.
- **E1 — Tool arguments:** each framework integration explicitly selects the
  argument facts safe to retain. Raw argument values are never copied by the
  generic PenguiFlow projector.
- **E2 — Decision reasons:** integrations emit short reason codes derived from the
  selected operation. Planner thoughts and chain-of-thought are not evidence.
- **E3 — Tool results:** integrations inspect raw results inside the trusted agent
  process and emit pass/fail checks. Raw rows and observations stay in the native
  trace.
- **E4 — Final-answer accuracy:** `final_answer_accuracy.v1` weights factual and
  numerical correctness (40%), scope (20%), grounding (15%), completeness (15%),
  and interpretation correctness (10%). Interpretation is checked only when the
  integration has structured expected evidence; otherwise it is not applicable
  and is excluded from the denominator. The minimum score is 0.85, the primary
  factual criterion must be fully correct, and hard failures always reject the
  answer.
- **E5 — Verified mining:** `CandidateMiner` requires both `successful=True` and
  `verified_success=True`. A run that merely completed is excluded.
- **E6 — Verification:** tests cover redaction, tool checks, rubric math, hard
  failures, MLflow feedback records, verified mining, and the synthetic
  baseline-versus-skill loop.

## Final-answer hard failures

An answer fails regardless of its weighted average when it has one of these safe
reason codes:

- `primary_result_incorrect`
- `wrong_scope`
- `invented_evidence`
- `false_data_unavailable`
- `policy_violation`
Policy compliance, latency, cost, tool-error rate, clarity, customer correction,
and other outcome metrics remain separately visible. A promotion policy can
protect them without hiding them inside the accuracy score.

`PromotionPolicy.candidate_metric_thresholds` supplies absolute gates as well as
baseline-versus-candidate comparisons. For example, a policy can require final
answer accuracy of at least `0.85`, task success of `1.0`, policy compliance of
`1.0`, and tool-error rate of `0.0` on a small deterministic held-out set.

## Storage boundary

The trusted host may inspect raw arguments, observations, expected facts, and the
final answer while calculating checks. The portable investigation stores only:

- allowlisted argument names and structural facts;
- operation reason codes;
- check names, statuses, and reason codes;
- rubric version, criterion scores, hard failures, and an assessment reference.

The Campaign integration also writes those scores as MLflow trace feedback. The
MLflow assessment metadata links the rubric, source trace, and investigation, but
does not include the answer or expected values.

For candidate drafting, the offline reader projects only this safe verification
record into each `TraceLearningRecord`. The pattern sent to a drafting model may
contain tool names, allowlisted argument facts, decision reason codes, check
outcomes, and rubric outcomes. Unknown extension fields are dropped. Raw prompts,
answers, filter values, observations, and source trace IDs are not included in
the drafting prompt.

## Conservative limitation

The current Campaign deterministic answer checker can fully verify bounded
aggregate metric answers. It records grounding as partial because a deterministic
number matcher cannot prove every sentence is supported. Complex narrative,
chart, and open-ended answers remain unverified until a task-specific deterministic
checker, constrained judge, or human assessment is registered. They are not
treated as successful mining evidence by default.
