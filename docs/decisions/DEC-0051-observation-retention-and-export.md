# DEC-0051 — Observation retention and export

- **ID:** DEC-0051
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-11-19T17:19:47+00:00
- **Scope:** observability
- **Supersedes:** none

## Decision

Keep closed records local with no external exporter initially. Propose 30 days for derived diagnostic detail; retain sanitized incident facts and version/evaluation/approval/rollout provenance until explicit retirement. Use bounded explicit backfill.

## Rationale

Existing storage is local and new telemetry must redact before persistence/export without importing private provider transcripts.

## Impact

New retention/deletion behavior needs exact approved policy; no external collector or raw transcript storage.
