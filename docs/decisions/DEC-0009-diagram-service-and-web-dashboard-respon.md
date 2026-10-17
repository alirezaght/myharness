# DEC-0009 — Diagram Service and Web Dashboard responsibilities

- **ID:** DEC-0009
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:13:40+00:00
- **Scope:** component_responsibilities
- **Supersedes:** none

## Decision

The Diagram Service owns diagram generation and revision; the Web Dashboard owns review, comments and approvals. Neither may bypass the approval gate, and neither edits diagrams directly.

## Rationale

"Generates and revises architecture diagrams." (docs/SPEC.md §13 Diagram Service, line 829); "Provides architecture review, monitoring, and approvals." (docs/SPEC.md §13 Web Dashboard, line 849); "- No bypass of approval gates through automatic execution modes" (docs/SPEC.md §11, line 751); "Direct diagram editing is not required for V1." (docs/SPEC.md §8.6, line 605)

## Impact

Clear ownership; approvals stay behind the same gate as the CLI.
