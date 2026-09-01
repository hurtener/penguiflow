# Investigation trajectory contract, v1

`InvestigationTrajectoryV1` is the portable, redacted evidence document used to
discover recurring successful procedures and failures across agent frameworks.
It is not a replay format, an evaluation-row schema, or a replacement for a
native trace.

## Required identity and location fields

Every document contains `schema_version`, `investigation_id`, `source_trace_ref`,
`agent_ref`, `provider_ref`, `scope_ref`, `started_at`, `status`,
`execution_fingerprint`, `request`, `steps`, and `redaction_profile`.

`source_trace_ref` is complete without reading the native run. It contains:

```text
tracking_store_ref
experiment_id
mlflow_trace_id
deployment_ref
native_trace_id (optional)
```

The first three fields locate the MLflow trace; `deployment_ref` pins the agent
bundle that produced it. `native_trace_id` links to the framework's own trace
when it exists.

## Portable shape

The full document has the fields proposed in
`docs/proposals/MLFLOW_LEARNING_CONTROL_PLANE_FINAL.md`: intent, model and
execution context, bounded input/artifact/source references, ordered steps and
events, outcome and assessment references, plus provider-namespaced extensions.
Content-bearing fields contain only allowlisted, bounded, redacted values or
opaque references. They never contain credentials, unrestricted customer
content, or raw chain-of-thought.

## Canonical bytes and digest

`InvestigationTrajectoryV1.canonical_bytes()` is the sole digest input. It is:

- UTF-8 encoded without a byte-order mark;
- JSON with lexicographically sorted object keys and no insignificant
  whitespace;
- list order preserved exactly;
- timestamps normalized to UTC with six fractional digits and a `Z` suffix;
- finite numeric values only; floats render as normalized base-10 decimal
  values without exponent notation; and
- hashed as `sha256:<hex>` over those exact bytes.

The canonical contract intentionally says nothing about where those bytes are
stored or transported.

## Query index

The document produces only these string-valued discovery fields:

```text
schema
investigation id
agent ref
provider ref
scope ref
status
execution fingerprint
intent class
step signature
outcome presence
assessment presence
```

The control plane queries this index before reading a document. It never scans
document bodies to discover candidates.
