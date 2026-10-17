# DEC-0015 — No extra limits or caching for the dashboard

- **ID:** DEC-0015
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:20:17+00:00
- **Scope:** performance
- **Supersedes:** none

## Decision

No limits, caching or batching beyond generating only the relevant diagrams; comments stay unlimited (Q5).

## Rationale

"Only relevant diagrams should be generated." (docs/SPEC.md §3 Phase 4, line 157); developer on comment limits: "q5 no limit"

## Impact

Simple implementation; no cache invalidation concerns.
