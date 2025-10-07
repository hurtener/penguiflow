```mermaid
graph TD
    bug_plan["bug_plan"]
    status_updates["status_updates"]
    deliver_final["deliver_final"]
    Rookery["Rookery"]
    compose_final["compose_final"]
    parse_documents["parse_documents"]
    extract_metadata["extract_metadata"]
    collect_logs["collect_logs"]
    OpenSea["OpenSea"]
    start["start"]
    triage["triage"]
    run_diagnostics["run_diagnostics"]
    chunk_sink["chunk_sink"]
    generate_summary["generate_summary"]
    propose_fix["propose_fix"]
    render_report["render_report"]
    documents_plan["documents_plan"]
    classDef endpoint fill:#e0f2fe,stroke:#0369a1,stroke-width:1px
    class Rookery endpoint
    class OpenSea endpoint
    bug_plan --> status_updates
    deliver_final -->|egress| Rookery
    compose_final --> status_updates
    parse_documents --> extract_metadata
    collect_logs --> status_updates
    parse_documents --> status_updates
    OpenSea -->|ingress| start
    start --> triage
    collect_logs --> run_diagnostics
    extract_metadata --> status_updates
    start --> status_updates
    run_diagnostics --> status_updates
    chunk_sink -->|egress| Rookery
    extract_metadata --> generate_summary
    triage --> bug_plan
    run_diagnostics --> propose_fix
    generate_summary --> render_report
    triage --> status_updates
    generate_summary --> status_updates
    propose_fix --> status_updates
    triage --> documents_plan
    render_report --> status_updates
    propose_fix --> compose_final
    documents_plan --> status_updates
    render_report --> compose_final
    documents_plan --> parse_documents
    compose_final --> deliver_final
    status_updates -->|egress| Rookery
    bug_plan --> collect_logs
    compose_final --> chunk_sink
```
