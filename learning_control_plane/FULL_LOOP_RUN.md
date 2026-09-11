# Run the Learning Control Plane MVP

This guide runs the complete Learning Control Plane (LCP) MVP locally. It uses
safe synthetic evidence and deterministic evaluation, so it does not require
MLflow, a model API key, or a running agent service.

The run demonstrates the full governed loop:

1. Mine a repeated successful pattern from safe evidence.
2. Draft an advisory skill.
3. Evaluate the normal agent and the skill-assisted agent on later held-out cases.
4. Apply the promotion gate.
5. Record a human approval.
6. Deliver the approved skill to one tenant scope.
7. Persist an authorization and activation receipt.

## Prerequisites

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/)

From a fresh clone:

```bash
git clone https://github.com/hurtener/penguiflow.git
cd penguiflow
uv sync --group dev
```

## Run the complete loop

Create a new empty temporary directory for the run, then execute the local demo:

```bash
lcp_demo_directory=$(mktemp -d /private/tmp/penguiflow-lcp-demo.XXXXXX)

uv run python examples/learning_control_plane_local_demo/flow.py \
  --db-directory "$lcp_demo_directory"
```

The command prints identifiers similar to:

```json
{
  "candidate_id": "candidate_...",
  "job_id": "job_...",
  "authorization_id": "auth_...",
  "receipt_id": "receipt_...",
  "control_plane_db": ".../control-plane.db",
  "skills_db": ".../skills.db"
}
```

The IDs prove that the candidate, offline evaluation, promotion decision, review,
delivery authorization, and activation receipt were created. The two SQLite files
retain the durable control-plane state and the delivered scoped skill.

## Inspect the durable state

Use the paths printed by the demo:

```bash
sqlite3 "$lcp_demo_directory/control-plane.db" \
  "SELECT payload FROM lcp_state WHERE id = 1;"

sqlite3 "$lcp_demo_directory/skills.db" ".tables"
```

The control-plane database stores the candidate, evaluation result, gate decision,
review, authorization, and receipt. The skills database stores the delivered
advisory skill under its approved scope.

## Verify the implementation

```bash
uv run pytest tests/learning_control_plane -q
```

## What this demo does not do

The local demo proves the LCP workflow, not production learning quality. A real
integration must supply redacted production investigations, a constrained skill
drafter, frozen held-out cases, and domain-specific scoring before a skill can be
considered for delivery. The LCP remains offline: failure of this workflow cannot
interrupt an agent serving users.
