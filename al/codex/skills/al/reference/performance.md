# Runtime and test performance

## Stage gate execution

Use one `gates run --stage base` or `gates run --stage candidate` invocation for the full stage. Each invocation shares a disposable checkout across its applicable obligations. Equivalent successful local checks may share execution when their full declared dependency identities match. Every obligation retains separate evidence and provenance. Remote checks remain independent. Ignored-only scratch output invalidates checkout reuse and causes lazy rematerialization from the pinned source candidate.

Complete candidate capture remains complete, including vendored directories. Narrow scopes are appropriate only when supported by sourced dependencies and do not remove any path from candidate identity.

After a successful gate, ignored generated symlinks are inspected with `lstat`/`readlink` semantics and are never followed. The gate evidence remains valid when only ignored output changed, but the disposable checkout and reuse cache are immediately discarded. Initial capture, candidate overlay, launch-boundary symlinks, nonignored changes, and execution-relevant metadata remain strict failures.

## Test setup

The standard isolated test repository is initialized and committed once per pytest process, then copied into each test's private temporary directory. This preserves per-test repository, runtime, and environment isolation while avoiding repeated `git init`, `git add`, and `git commit` process startup. No semantic regression tests are removed: review found no exact duplicate whose deletion preserved a distinct realistic failure scenario. The checkout, cancellation, recovery, shell policy, and evidence-integrity suites remain separate because each protects a different failure mode. The two Codex checks that required a machine-installed config (`test_codex_dir_resolves_where_expected` and `test_config_toml_registers_every_agent_type_and_nothing_else`) were removed because they tested ambient installation state rather than the selected package; shipped role/model/skill policy checks remain. Claude's native hook and immutable-session integration regressions remain in its suite. Heavy shared-flow modules that were byte-identical in both suites (runtime batches 4, 5, 6-recovery, 7, 12-replay, 13-remote, 13-checkout, 14-consumers, 16-writers, gitlink review/capabilities, execution receipts and gate reuse) run once, in the Codex suite: the parity test proves every shared script byte-identical across distributions, and the Claude suite keeps its platform-adapter, session, store, native and parity tests. The end-to-end remote malformed-response matrix runs representative shapes; the unit schema test covers every shape.

Test resource helpers resolve the selected package directly for both versioned (`al/<platform>/skills/al`) and installed (`<platform>/skills/al`) layouts. Versioned tests read role resources from this repository's matching platform directory; they never fall back to machine-wide configuration. Repository behavior fixtures are checked in as data and copied into disposable test repositories, so discovery tests do not depend on another project checkout.

## Resident service

`alctl run` keeps a resident AL service per repository (Git common directory), pinned runtime and launcher installation (#27). The first command of a run whose pin carries the service starts it in the background and still runs in process; later commands of the run reach it through a thin client at the top of the managed launcher, so a warm command costs one interpreter start and one socket round trip instead of re-verifying the launcher and the pin and starting the runtime again.

- **Trust.** The service verifies the pinned runtime once, when it starts, with the same verification text the one-shot run child uses, and checks the live interpreter. A tampered pin never starts a service. A running service watches its pin directory and re-verifies on any change; a failure stops it. Every command re-reads and re-validates the run's protected binding, and a run pinned to another runtime is never executed by it, so runs pinned to different versions never share a process.
- **Socket and token.** Each service lives in `<AL state>/al-services/<hash>/` (directories 0700, files 0600) beside the run-pin store. The client checks ownership and modes and the peer's user id, and sends the command with the service's run-authentication token, its working directory, environment, umask and standard streams (passed as descriptors, so output streams directly).
- **Same results.** Each command runs in a child forked from the service. Every AL module is imported afresh from the verified bytes (only compiled code and standard-library modules are reused), and the child runs the one-shot program's own command tail, so stdout, stderr, tracebacks and exit status are those of the in-process run. Anything the service cannot vouch for (no or inactive binding, another pin, another launcher, a different locale, an unsupported option such as `--existing-run`) is answered before the command starts, and the launcher continues in process. `start` and `clear` always run in process.
- **In memory.** A file watcher (FSEvents on macOS, inotify on Linux) keeps each worktree's snapshot tree current; the service recomputes it in the background once changes settle. When a command takes a snapshot it asks the service, which answers only if a cookie written into the Git directory comes back through the watcher with no relevant event since the tree was computed; otherwise the command takes the snapshot itself. Fact-store pack headers are reused for the same immutable files. The service never runs checks or records evidence on its own.
- **Lifecycle.** `alctl service status [--repository R]` lists services (pin, worktrees, snapshot state, commands served); `alctl service stop [--repository R]` stops them. A service exits after `AL_SERVICE_IDLE_SECONDS` (default 1800) without commands. It persists nothing but its socket, token and log, so a crash leaves nothing to repair: the next command runs in process and starts a new one. `AL_SERVICE=0` disables the service for a command (and the test suites set it).

Measured on a loaded development machine: a warm `show --json` round trip takes about 0.1 s through the service against about 0.5-0.7 s in process, on a small repository and on a 17,000-file synthetic monorepo alike. A clean snapshot of the 17,000-file repository costs about 110 ms with `git add -A`; the service answers it in about 1 ms.

## Warm command overhead (#25)

Each command observes the working copy through its snapshot tree, and repeated questions inside one command are answered from one stability-checked observation:

- **Decision windows.** A read-only decision (`S.decision`: readiness, gate currency, review completeness, discovery and its validation) is bracketed by two observations of the working copy: the snapshot tree plus the state a tree does not record (index, HEAD, physical modes, untracked members, content Git may convert or AL's membership excludes). Inside, the candidate records, the task-attribution classification and the membership listing of one question are read once; equal observations prove nothing moved, otherwise the decision is evaluated again without any reuse. A plain attribution capture inside a window is read once (the window's closing observation is its stability proof).
- **Candidate records.** The records are a function of what an observation sees, so a bracket opening on an observation equal to an earlier confirmed one reuses its records, and one opening on a different observation re-reads only the paths whose observed state differs. The latest confirmed records of a worktree are kept in the run's runtime directory (`observed-records/`, keyed by worktree and base, bound to the code digest and to the digest of the observation they were read under), so the next command starts from them. A changed HEAD, gitlinks, a forged or unreadable record, or `AL_ANALYSIS_CACHE=0` read every record afresh.
- **Recorded answers.** Inventories and gate-currency answers are recorded with the capture observations they read (see the gates reference) and replayed while that capture re-observes unchanged.
- **Snapshot refs.** Approval releases the run's `refs/al/snapshots/<run>/` refs; each new run sweeps refs no pin touched for 30 days.

A changed task file still costs one new inventory of the planned question (the membership listing, the capture and the analysis of the changed blobs; facts of unchanged blobs come from the fact store).

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
