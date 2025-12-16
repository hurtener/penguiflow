# A2A Spec Files (Repo Notes)

This directory contains a local copy of the A2A specification material used to ground PenguiFlow’s A2A implementation plan.

## Files

- `docs/spec/a2a_specification.md`: A2A Protocol Specification content (DRAFT v1.0 document, with “Latest Released Version 0.3.0” note). This file includes template directives that reference an A2A proto file for field tables.
- `docs/spec/a2a.proto`: Local copy of the A2A proto used to ground PenguiFlow’s implementation plan.
- `docs/specification/grpc/a2a.proto`: Copy of the same proto placed here to satisfy the include paths used by `docs/spec/a2a_specification.md` (it references `specification/grpc/a2a.proto`).

## Proto grounding

To keep the spec self-contained and eliminate drift between documentation and implementation:

1. Vendor/pin the normative proto:
   - Primary copy: `docs/spec/a2a.proto`
   - Render-helper copy: `docs/specification/grpc/a2a.proto`
2. Record the source and exact version (tag + commit SHA) in the repository (e.g., in the file header or a short `docs/spec/VERSION.md`).
3. When the proto updates, update both copies and revalidate:
   - `docs/A2A_COMPLIANCE_GAP_ANALYSIS.md` (wire shapes and rules)
   - any generated models/schemas (if added in the future)

## Workflow suggestion

- Treat proto changes as “spec upgrades” with explicit review.
- Keep a short changelog of protocol-impacting changes (fields added/renamed/deprecated) to reduce accidental breaking changes.
