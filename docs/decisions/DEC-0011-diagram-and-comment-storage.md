# DEC-0011 — Diagram and comment storage

- **ID:** DEC-0011
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:17:10+00:00
- **Scope:** data_schemas
- **Supersedes:** none

## Decision

A new migration adds diagram versions (built from a structured model with stable ids for components and connections) and comments tied to the element id and diagram version, with states open, addressed, resolved. No existing table changes.

## Rationale

"Diagrams come from a structured model (stable ids for components and connections) so comments can be anchored to them" (tasks/phase-2-visual-design-studio.md P2-04, line 27); "Comments persist in task state and are tied to the element id and the diagram version" (line 34); "Comment states: open → addressed → resolved" (line 35)

## Impact

Additive schema change only; Phase 1 tables untouched.
