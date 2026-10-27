# Playground Staged Changes Adversarial Audit

## Remediation Update

The original staged changes have since been extended and re-verified. Resolved items:

- package-scoped exports now use deterministic standard, dotted, `src/`, package-root, and mixed-layout resolution
- lazy and partially initialized planners enrich runtime metadata after startup
- runtime metadata reports configured agent name and reflection state
- dataset browse selects one deterministic default, preferring first sorted `evaluate.spec.json` reference, then runnable data
- browse excludes outside-project symlinks and stops inspection after first `val` row
- generated environment guides and Playground command documentation use `--project-root`

Deferred risks remain separate work: metric import allowlisting, real redaction policies, malformed JSONL API errors, split semantics, eval cap allocation, and concurrent export reservation.

## Scope

This audit reviews staged changes to:

- `penguiflow/cli/playground.py`
- `tests/cli/test_playground_endpoints.py`

Review focus:

- normal `penguiflow dev` behavior
- spec-driven and orchestrator-driven agents
- standard, dotted, and `src/` package layouts
- Playground eval dataset discovery, export, and default selection
- enterprise v2 example compatibility
- documentation and command parity

## Verdict

The staged changes are not ready to merge.

Focused verification produced:

- 2 failed tests
- 153 passed tests
- Ruff passed
- targeted mypy passed

Both failures cover documented default dataset export paths. Additional metadata and package-layout behavior introduced by the staged changes lacks regression coverage.

## Findings

### 1. Critical: package-scoped export paths regress

**Location:** `penguiflow/cli/playground.py:996-1005`

`_resolve_evals_root()` now returns the first candidate that already exists. When `agent_package` is supplied but its `evals/` directory does not exist yet, resolution falls back to `<project_root>/evals`.

That behavior is wrong for export. Export is responsible for creating the package-scoped directory and must not require it to exist first.

Broken contracts:

- standard layout: `<project_root>/<agent_package>/evals/playground_export/dataset`
- src layout: `<project_root>/src/<agent_package>/evals/playground_export/dataset`

Failing tests:

- `test_export_dataset_uses_default_agent_evals_path_when_output_dir_missing`
- `test_export_dataset_default_dir_uses_src_layout_agent_package`

Documentation currently promises these paths in:

- `docs/cli/dev-command.md`
- `docs/cli/playground-evals.md`
- `docs/cli/react-planner-evals.md`
- `docs/cli/eval-command.md`

#### Proposed remediation

Separate read and write resolution:

- `_resolve_evals_browse_root()` may select an existing fallback directory.
- `_resolve_default_eval_export_root()` must deterministically return the package-scoped path, whether or not it exists.

For dotted package names, convert module segments to path segments:

```python
package_dir = Path(*package_name.split("."))
```

Add tests for:

- package directory without existing `evals/`
- existing package `evals/`
- dotted package name
- `src/` layout
- project root that is itself the agent package
- no `agent_package`

### 2. High: lazy orchestrators remain unidentified

**Locations:**

- `penguiflow/cli/playground.py:1010-1057`
- `penguiflow/cli/playground.py:1087-1088`

`_enrich_meta_from_planner()` runs before FastAPI lifespan calls `agent_wrapper.initialize()`.

Orchestrators that create `_planner` in `_ensure_initialized()` therefore have no planner when enrichment runs. Their UI remains `unknown_agent` with no catalog for the entire process because metadata is not recomputed.

#### Proposed remediation

Move metadata enrichment after wrapper initialization.

Preferred options:

1. Initialize wrapper before constructing runtime metadata.
2. Build `/ui/meta` dynamically from initialized runtime state.
3. Refresh cached metadata in lifespan immediately after `await agent_wrapper.initialize()`.

Add tests for:

- eager orchestrator planner
- lazy orchestrator planner
- direct `PlannerAgentWrapper`
- wrapper without an accessible planner
- spec-driven project where spec metadata remains authoritative

### 3. High: runtime metadata reports false configuration

**Locations:**

- `penguiflow/cli/playground.py:1035-1047`
- `penguiflow/cli/playground.py:430-478`

Runtime enrichment populates tools, `max_iters`, `hop_budget`, and `absolute_max_parallel`, but leaves other values from `_meta_from_spec(None)` unchanged.

Observed enterprise v2 contradiction:

- runtime reflection is enabled
- Playground reports `reflection: false`

The staged comment says runtime enrichment supports "tools/flows," but flows remain zero. Services, rich-output state, template, and flags also remain unknown or empty.

#### Proposed remediation

Create one explicit runtime metadata adapter instead of partially mutating spec metadata.

Derive only values supported by stable runtime contracts. At minimum:

- planner limits
- reflection enabled state
- visible tool catalog
- configured agent name when available

Do not claim flows or services unless they can be derived accurately.

Prefer public properties or a dedicated planner metadata method over direct private-attribute access. If private access remains necessary, centralize it in one tested adapter.

### 4. Medium: configured agent identity is ignored

**Location:** `penguiflow/cli/playground.py:1048-1055`

Agent name is synthesized from orchestrator class name. `EnterpriseAgentOrchestrator` becomes `enterpriseAgent`, while enterprise v2 config declares `enterprise_agent_v2`.

Template remains `unknown`, producing internally inconsistent identity metadata.

#### Proposed remediation

Use this precedence:

1. spec agent name
2. runtime config `agent_name`
3. explicit orchestrator metadata property
4. class-name fallback

Use snake-case conversion for class fallback rather than lowercasing only the first character.

### 5. Medium: default dataset selection uses directory-name policy

**Location:** `penguiflow/cli/playground.py:2464-2470`

The staged change marks every dataset below `playground_export` as non-default. This fixes the enterprise fixture because its old exported dataset has only `unknown` splits, but the rule does not inspect dataset validity.

Consequences:

- valid freshly exported datasets are always deprioritized when a canonical dataset exists
- invalid canonical datasets may still be preferred
- directory naming, rather than eval semantics, controls selection

This is fragile against the documented authoring loop: export traces, load the exported dataset, and run evals.

#### Proposed remediation

Use explicit precedence:

1. dataset referenced by an `evaluate.spec.json`
2. dataset manifest containing at least one `val` row
3. newest valid Playground export
4. first browsable dataset as fallback

Expose one preferred entry rather than marking every `dataset.jsonl` as default.

If selection rules remain path-based, document them explicitly and add tests covering projects with:

- only a Playground export
- canonical plus valid Playground export
- canonical plus invalid Playground export
- multiple evaluate specs
- no runnable dataset

### 6. Medium: staged behavior lacks direct regression coverage

**Location:** `tests/cli/test_playground_endpoints.py`

The staged test only checks that `playground_export` entries receive `is_default: false`.

Missing tests include:

- `_resolve_evals_root()` for dotted packages
- package-root fallback used by enterprise v2
- standard export path creation
- src-layout export path creation
- runtime metadata enrichment
- lazy initialization
- reflection metadata accuracy
- configured agent name precedence
- spec metadata preservation

#### Proposed remediation

Add focused tests before retaining either staged helper.

Minimum suite:

```text
eval browse roots
eval export roots
dotted package paths
src layouts
project-root package layouts
eager planner metadata
lazy planner metadata
spec metadata precedence
runtime reflection state
dataset default precedence
```

### 7. Documentation and command examples are out of sync

#### Enterprise v2 command

`examples/planner_enterprise_agent_v2/evals/policy_compliance_v1/README.md` currently shows:

```bash
uv run penguiflow dev --project-root examples/planner_enterprise_agent_v2
```

Current discovery requires an explicit package for this repo-root invocation:

```bash
uv run penguiflow dev \
  --project-root examples/planner_enterprise_agent_v2 \
  --agent-package examples.planner_enterprise_agent_v2
```

Alternatively, package discovery should be changed so the documented simpler command works again. Code behavior and docs must choose one contract.

#### CLI syntax

`docs/cli/dev-command.md` documents:

```bash
penguiflow dev [--project-root PATH] [--host HOST] [--port PORT] [--no-browser]
```

The implemented CLI also supports:

```text
--agent-package TEXT
```

The syntax and usage guidance should include it.

#### Eval runtime expectations

Enterprise v2 uses a large configured model plus reflection. A four-case eval can take several minutes because cases execute sequentially and each case may make multiple planner and reflection calls.

Docs should state:

- Eval runs are synchronous in the current Playground endpoint.
- `max_cases` bounds work but cannot select split position; truncating before validation rows may fail.
- Large models and reflection materially increase runtime.
- Start with the smallest runnable dataset during smoke testing.

## Recommended Implementation Sequence

### Increment 1: restore eval path contracts

- split browse-root and export-root resolution
- preserve standard, dotted, and src-layout package behavior
- make all existing export tests green

### Increment 2: make runtime metadata accurate

- enrich after initialization
- derive configured agent name
- include reflection state
- stop claiming unsupported flows/services
- add eager and lazy planner tests

### Increment 3: make dataset preference semantic

- prefer evaluate-spec referenced datasets
- validate split metadata before selecting defaults
- preserve export-first workflow when no canonical dataset exists
- add precedence tests

### Increment 4: synchronize documentation

Update:

- `docs/cli/dev-command.md`
- `docs/cli/playground-evals.md`
- `docs/cli/react-planner-evals.md`
- `docs/cli/eval-command.md`
- `examples/planner_enterprise_agent_v2/evals/policy_compliance_v1/README.md`
- any proposal or quickstart containing the enterprise v2 Playground command

## Verification Gate

Before merge, run:

```bash
uv run pytest tests/cli/test_playground_endpoints.py tests/cli/test_playground_helpers.py -q
uv run ruff check penguiflow/cli/playground.py tests/cli/test_playground_endpoints.py
uv run mypy penguiflow/cli/playground.py
```

Also perform smoke checks for:

```text
generated spec project
direct planner project
eager orchestrator project
lazy orchestrator project
standard package layout
src package layout
dotted package layout
enterprise v2 browse + metric discovery
enterprise v2 one-case runnable eval
dataset export followed by immediate rerun
```

## Out-of-Scope Risks Observed

The audit also surfaced pre-existing risks outside the staged diff:

- broad `TypeError` retry in orchestrator wrapper may execute side effects twice
- concurrent requests mutate shared planner event callback state
- automatic discovery does not recursively locate nested dotted packages
- exported manifests may record workload as `unknown`

These should be tracked separately so they do not expand this staged fix beyond its intended scope.
