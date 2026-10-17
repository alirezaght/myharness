# Decision records

Approved decisions for the harness itself, one Markdown file per decision. Each record carries the fields from SPEC §6.4: id, content (Decision), rationale, scope, status, version, timestamp, supersedes and impact.

Records are written by the `harness decision` and `harness interview decide` commands, which keep the same format. When a decision is changed, the previous version moves to [`history/`](history/) with status `superseded`, and the new version keeps the ID. The table below indexes the harness's own records; `harness decision list` is the programmatic index.

| ID | Title | Status | Version | Date |
|---|---|---|---|---|
| [DEC-0001](DEC-0001-runtime-and-language.md) | Runtime and language | approved | 1 | 2026-10-16 |
| [DEC-0002](DEC-0002-packaging-and-entry-point.md) | Packaging and entry point | approved | 1 | 2026-10-16 |
| [DEC-0003](DEC-0003-sqlite-access.md) | SQLite access | approved | 1 | 2026-10-16 |
| [DEC-0004](DEC-0004-testing-lint-ci.md) | Testing, lint and CI | approved | 1 | 2026-10-16 |
| [DEC-0005](DEC-0005-phase-2-scope-boundary.md) | Phase 2 scope boundary | approved | 1 | 2026-10-17 |
| [DEC-0006](DEC-0006-phase-1-cli-flow-stays-unchanged-alongsi.md) | Phase 1 CLI flow stays unchanged alongside the dashboard | approved | 1 | 2026-10-17 |
| [DEC-0007](DEC-0007-dashboard-outputs-and-side-effects.md) | Dashboard outputs and side effects | approved | 1 | 2026-10-17 |
| [DEC-0008](DEC-0008-concurrent-cli-and-dashboard-operations.md) | Concurrent CLI and dashboard operations | approved | 1 | 2026-10-17 |
| [DEC-0009](DEC-0009-diagram-service-and-web-dashboard-respon.md) | Diagram Service and Web Dashboard responsibilities | approved | 1 | 2026-10-17 |
| [DEC-0010](DEC-0010-dashboard-component-communication.md) | Dashboard component communication | approved | 1 | 2026-10-17 |
| [DEC-0011](DEC-0011-diagram-and-comment-storage.md) | Diagram and comment storage | approved | 1 | 2026-10-17 |
| [DEC-0012](DEC-0012-local-dashboard-http-api.md) | Local dashboard HTTP API | approved | 1 | 2026-10-17 |
| [DEC-0013](DEC-0013-no-feature-flag-for-the-dashboard.md) | No feature flag for the dashboard | approved | 1 | 2026-10-17 |
| [DEC-0014](DEC-0014-failures-are-handled-by-retry.md) | Failures are handled by retry | approved | 1 | 2026-10-17 |
| [DEC-0015](DEC-0015-no-extra-limits-or-caching-for-the-dashb.md) | No extra limits or caching for the dashboard | approved | 1 | 2026-10-17 |
| [DEC-0016](DEC-0016-only-the-local-developer-may-act-in-the.md) | Only the local developer may act in the dashboard | approved | 1 | 2026-10-17 |
| [DEC-0017](DEC-0017-no-alerting-for-the-local-dashboard.md) | No alerting for the local dashboard | approved | 1 | 2026-10-17 |
| [DEC-0018](DEC-0018-phase-2-verification-approach.md) | Phase 2 verification approach | approved | 1 | 2026-10-17 |
