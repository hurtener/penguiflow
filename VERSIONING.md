# Versioning and deprecation policy

PenguiFlow aims to follow **Semantic Versioning** (SemVer) for the Python package version.

## What “public API” means

The public API surface is the set of names exported from `penguiflow.__init__` and documented on the docs site.

Anything else (internal modules, RFCs/proposals, private attributes) may change without notice.

## Deprecations

- Deprecations should be announced in the changelog and documented in the docs.
- Deprecated APIs should emit a warning where feasible.
- Removal typically occurs in a subsequent **minor** or **major** release depending on impact.

## Compatibility goals

- Patch releases: bug fixes only, no breaking changes.
- Minor releases: new features; breaking changes avoided but may happen with a clear migration path.
- Major releases: breaking changes allowed with migration guidance.

