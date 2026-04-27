# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added
- Enterprise-grade documentation site (MkDocs) and doc CI checks.
- Experimental A2A router continuity support for Phase 0/1, including specialist-side `a2a_context_id`
  session precedence, outbound A2A `contextId` support, and StateStore-backed remote conversation bindings.
- Experimental A2A router Phase 2/3 support, including normalized remote task lifecycle APIs, input/auth-required
  planner pause mapping, push notification config client helpers, agent registry scoring, and router delegation tools.
- Experimental A2A router Phase 4/5 API freeze candidate, including router policy guardrails, declarative registry
  loading, per-agent A2A auth headers, route decision metadata, and tag-triggered PyPI prerelease publishing.

### Changed
- Root README rewritten to be a concise “front door” with stable links.

## 2.12.1

Initial entry for the current packaging version. Prior release notes are being backfilled.
