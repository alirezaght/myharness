# Decision records

Approved decisions for the harness itself, one Markdown file per decision. Each record carries the fields from SPEC §6.4: id, content (Decision), rationale, scope, status, version, timestamp, supersedes and impact.

Records are written by the `harness decision` and `harness interview decide` commands, which keep the same format. When a decision is changed, the previous version moves to [`history/`](history/) with status `superseded`, and the new version keeps the ID. The table below indexes the harness's own records; `harness decision list` is the programmatic index.

| ID | Title | Status | Version | Date |
|---|---|---|---|---|
| [DEC-0001](DEC-0001-runtime-and-language.md) | Runtime and language | approved | 1 | 2026-10-16 |
| [DEC-0002](DEC-0002-packaging-and-entry-point.md) | Packaging and entry point | approved | 1 | 2026-10-16 |
| [DEC-0003](DEC-0003-sqlite-access.md) | SQLite access | approved | 1 | 2026-10-16 |
| [DEC-0004](DEC-0004-testing-lint-ci.md) | Testing, lint and CI | approved | 1 | 2026-10-16 |
