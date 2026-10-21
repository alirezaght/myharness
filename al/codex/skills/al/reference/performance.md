# Runtime and test performance

## Stage gate execution

Use one `gates run --stage base` or `gates run --stage candidate` invocation for the full stage. Each invocation shares a disposable checkout across its applicable obligations. Equivalent successful local checks may share execution when their full declared dependency identities match. Every obligation retains separate evidence and provenance. Remote checks remain independent. Ignored-only scratch output invalidates checkout reuse and causes lazy rematerialization from the pinned source candidate.

Complete candidate capture remains complete, including vendored directories. Narrow scopes are appropriate only when supported by sourced dependencies and do not remove any path from candidate identity.

After a successful gate, ignored generated symlinks are inspected with `lstat`/`readlink` semantics and are never followed. The gate evidence remains valid when only ignored output changed, but the disposable checkout and reuse cache are immediately discarded. Initial capture, candidate overlay, launch-boundary symlinks, nonignored changes, and execution-relevant metadata remain strict failures.

## Test setup

The standard isolated test repository is initialized and committed once per pytest process, then copied into each test's private temporary directory. This preserves per-test repository, runtime, and environment isolation while avoiding repeated `git init`, `git add`, and `git commit` process startup. No semantic regression tests are removed: review found no exact duplicate whose deletion preserved a distinct realistic failure scenario. The checkout, cancellation, recovery, shell policy, and evidence-integrity suites remain separate because each protects a different failure mode. The two Codex checks that required a machine-installed config (`test_codex_dir_resolves_where_expected` and `test_config_toml_registers_every_agent_type_and_nothing_else`) were removed because they tested ambient installation state rather than the selected package; shipped role/model/skill policy checks remain. Claude's native hook and immutable-session integration regressions remain in its suite.

Test resource helpers resolve the selected package directly for both versioned (`al/<platform>/skills/al`) and installed (`<platform>/skills/al`) layouts. Versioned tests read role resources from this repository's matching platform directory; they never fall back to machine-wide configuration. Repository behavior fixtures are checked in as data and copied into disposable test repositories, so discovery tests do not depend on another project checkout.

## Further opportunities

- Avoid rewriting candidate files in a scratch checkout when their captured bytes and modes already match the pinned tree entry; retain the existing post-overlay representation check.
- Cache indexed blob digests across captures, with invalidation bound to repository identity, object format, index OIDs, and replacement-object configuration.

## Benchmark record

The before/after benchmark is recorded as E19 in the AL evidence ledger. Separate interpreters compared each preserved global implementation with its repository candidate against the same temporary Git repository containing 1,200 distinct committed blobs. All 1,200 records matched exactly for both platforms, and both implementations materialized the same 1,201 filesystem entries. The measurement was:

| Platform | Operation | Preserved global | Repository candidate | `cat-file` processes |
| --- | --- | ---: | ---: | ---: |
| Codex | Record capture | 12.77 s | 0.185 s | 1,200 → 1 |
| Codex | Base-stage materialization | 13.21 s | 0.468 s | 1,200 → 1 |
| Claude | Record capture | 13.30 s | 0.187 s | 1,200 → 1 |
| Claude | Base-stage materialization | 13.80 s | 0.475 s | 1,200 → 1 |

Runtime-only evidence scopes keep documentation edits from invalidating that measurement.
