# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 2.12.5

### Added
- Enterprise-grade documentation site (MkDocs) and doc CI checks.
- Playground fixed-session controls via Setup tab and env config:
  - `PLAYGROUND_FIXED_SESSION_ID`
  - `PLAYGROUND_REWRITE_AGUI`
- Generated project templates now include fixed-session Playground env keys in `.env.example`.

### Changed
- Root README rewritten to be a concise “front door” with stable links.
- `penguiflow dev` now auto-discovers project state-store builders and wires the resulting store into Playground state/task persistence.
- Memory URL compatibility for discovered legacy builders now relies on `MEMORY_BASE_URL` with internal alias bridging.

## 2.12.1

Initial entry for the current packaging version. Prior release notes are being backfilled.
