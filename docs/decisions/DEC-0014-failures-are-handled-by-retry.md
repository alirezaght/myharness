# DEC-0014 — Failures are handled by retry

- **ID:** DEC-0014
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:18:45+00:00
- **Scope:** error_handling
- **Supersedes:** none

## Decision

Failures are handled by retry.

## Rationale

Developer statement: "failure should be hadled by retry". The sources do not settle it; docs/SPEC.md §12 lists failure handling per area but not for the dashboard.

## Impact

Dashboard operations retry on failure instead of failing fast.
