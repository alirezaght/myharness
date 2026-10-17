# DEC-0013 — No feature flag for the dashboard

- **ID:** DEC-0013
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:17:17+00:00
- **Scope:** configuration
- **Supersedes:** none

## Decision

No feature flag: the dashboard only runs when launched from /harness, and the CLI is unaffected.

## Rationale

"The Harness activates only when explicitly invoked by the developer." (docs/SPEC.md §2, line 46); "Lightweight local server plus frontend, launched from `/harness` (opens in the browser)" (tasks/phase-2-visual-design-studio.md P2-01, line 10)

## Impact

Explicit invocation is the switch; no extra configuration.
