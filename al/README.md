# AL source and release model

This directory is the canonical, version-controlled AL source package in this
repository. The same layout can be moved to a standalone repository later.
`al/release-layout.json` lists the runtime files that are shipped for Codex and
Claude; tests, caches, and build output are excluded.

`alctl` resolves numeric `vMAJOR.MINOR.PATCH` Git tags to immutable commits,
checks the selected source tree, creates a content-addressed release cache, and
then installs through AL's existing distribution ownership and transaction
engine. An enrolled installation keeps its source repository and subdirectory;
updates cannot silently switch either one. Project installations keep an
`al.lock.json` recording their selected release and update policy.

The default ownership registry is `~/.local/state/al-distribution/registry`;
installer metadata lives in its `installer/` child. `AL_DISTRIBUTION_STATE`
and `--state-root PATH` each select the ownership registry root directly.
`--home` selects only the global installation target. Legacy records remain
visible without automatic enrollment or migration.

For a clean release, commit the reviewed source and create a numeric version tag
such as `v1.2.0`. The AL release workflow runs installer tests, both platform
runtime suites, and a runtime-layout audit for version tags or manual dispatch.
Tagged verification binds the tested clean checkout to the exact tag and CI
commit. Development verification reports its dirty state without a release
claim. The workflow verifies; it does not publish files or create a tag.
Installers may use a local repository or a credential-free HTTPS Git URL.

Historical v0.1.0–v0.1.24 payloads keep their source-audited required-resource
contracts; newer helpers do not retroactively invalidate their caches or rollback
targets. Current and unversioned workspace payloads still require the complete
current layout. When required resources change, extend the trusted historical
contracts for newly published versions. The installer tests compare every
published tag with its genuine layout; a release's own declaration cannot lower
the trusted minimum.

The checkout's CLI can be bootstrapped into an independent local snapshot with
`python3 al/alctl.py bootstrap`. This records development provenance and does
not install or replace the AL runtime. See [INSTALL.md](INSTALL.md) for
installation, updates, review/adoption, diagnostics, and recovery.

## Verifying a change

Use one command to verify an AL change. Do not run plain `pytest` serially per host:

```sh
uv run python al/verify_changed.py                # fast tier for changes since merge-base with main, plus uncommitted and untracked files
uv run python al/verify_changed.py --base v0.1.30 # changes since another ref
uv run python al/verify_changed.py --path al/codex/skills/al/scripts/al_gates.py   # explicit files
uv run python al/verify_changed.py --list         # show the selection without running it
uv run python al/verify_changed.py --tier all     # also the affected slow tests
uv run python al/verify_changed.py --all --tier all   # every collected test, as a development run
```

### Test tiers

The **fast tier** is the default. Run it after every change. It contains the
affected test files that are not in the slow tier, plus the byte-parity,
consistency and docs guards of both hosts. A typical script change takes about
one minute. The worst case, every fast file of all three suites, took about
three minutes.

The **slow tier** contains the test files listed in `al/verification-tiers.json`
and the acceptance tier. These are end-to-end journeys, tests that build real
Git repositories or runtime pins, validator and launcher child processes, and
installer and release flows. They run only in `release_verify.py --full` (and
its acceptance step) or in a nightly run, never by default. Do not wait on them
after a change. A file is slow when its mean measured test time exceeds 1 s. To
re-measure, run a complete development run and record its report:

```sh
uv run python al/verify_changed.py --all --tier all --report /tmp/al-all/report.json
uv run python al/verify_changed.py --record-tiers /tmp/al-all/report.json
```

New test files count as fast until they are recorded. Keep new end-to-end tests
in files that are already slow, or record the tiers again.

### Writing fast tests

See [TESTS.md](TESTS.md) for the cost of each slow file, the invariant map of the
regression batches and the rules below.

- **Time budget.** `verify_changed.py` and `release_verify.py` fail a run when a test takes
  more than 5 s or a test file more than 60 s (setup + call + teardown, measured by the
  runner). Only the acceptance journeys in `al/verification-budget.json` are exempt; the
  total run time is still reported, not judged.

- Start from a prebuilt run instead of building one: a host test that requests the
  `run_state` fixture and calls `run_state("planned" | "implemented" | "finalizing" | "verified")`
  as its first action gets a private, byte-identical copy of an admitted run built once
  per process. Installer tests get the same for `launched`.
- Test a decision function directly; keep one public journey per outcome.
- Host-neutral tests (shared scripts through the `cli`, `state_file`, `launcher` and
  `run_state` adapters) live only in the Codex suite and are listed in
  `al/codex/skills/al/tests/single-run.json`; `test_single_run.py` checks that they never
  depend on host-specific code. Tests of host-specific scripts or host boundaries keep a
  copy in both suites.

It maps changed files to the test files that can observe them in `al/tests`
and both host suites. The mapping uses Python imports (including relative and
in-function imports), file names and module names quoted in source, and the
`scripts/al_<name>.py` to `tests/test_<name>.py` convention. `--depth N`
(default 1) also selects tests of modules that import a changed module, up to N
hops. A host change also runs the parity, consistency, resource and docs guards
of both hosts. Some changes fall back to complete suites: suite infrastructure
(a `conftest.py`, `al/verification_pytest.py`, `pyproject.toml`, `uv.lock`), a
deleted module, or an `al/` file that no test references. Changes outside `al/`
select no AL tests.

The selected files run through the release runner, `al/verification_runner.py`.
Each group of at most `--split` tests (default 8) from one file gets its own
pytest process with private HOME, TMP, runtime and harness state. At most
`--workers` (default 8) processes run at once. A group rebuilds its session and
module fixtures, so a long file no longer bounds the run; `--split 0` keeps
whole files. Test files whose isolation review is out of date also run in
parallel. Pass `--strict-isolation` to use the release grouping and
serialization of `release_verify.py --full`. Selected acceptance-tier files
(the long end-to-end proofs) run too. The command prints the failing tests with their pytest failure output and log paths,
the pass/fail counts and one final `PASS` or `FAIL` line. As in release
verification, a skipped test counts as not passed; it is listed separately. It also writes a JSON
report outside the checkout. The elapsed time is printed but never judged.
`--host codex|claude` limits a quick iteration to one host. Before you finish,
run both hosts.

This is a development check, not a release result. `release_verify.py --full`
remains the gate for a tag.

The Claude and Codex suites both run, even where the shared scripts are
byte-identical. Every host test runs through its host's own `conftest.py` and
host-specific `al_state.py`, `al_store.py`, `al_start.py` and `al_models.py`
(the autouse runtime fixture patches `al_store`, and the `cli` fixture drives
`al_state`). A passing Codex run therefore does not prove the Claude behavior.
Some long shared-flow suites already exist in only one host, for example
`test_gate_reuse.py` and `test_runtime_batch13_*.py` in Codex.

Phase 3 adds the shared `al-execution-v1` semantic event contract and guarded
task/blocker commands to both packages. A harness execution requires the
installed package to expose this protocol and its role resources; an older
installation reports an explicit compatibility failure. Read-only capabilities
and event replay are available with `al_state.py integration capabilities` and
`integration events --after N --limit N`. See each package's
[`reference/execution-integration.md`](codex/skills/al/reference/execution-integration.md)
for task admission, agent reports, scoped decisions and recovery. Source edits
do not replace an active global or project installation; install a reviewed
candidate through the existing ownership-aware `alctl` workflow.


Read-only agents must never request background execution, detached/daemonized
commands or persistent services such as database proxies; unmet service
prerequisites go to the supervisor. Claude's Bash hook accepts background
metadata only when absent or literal false and retains its shell restrictions.
This does not contain arbitrary foreground executables spawning descendants or
hosts automatically backgrounding work. Codex role instructions and filesystem
sandboxing are not a universal process sandbox. No automatic process cleanup is
claimed; existing role permissions remain unchanged.

Every supported writer dispatch now uses `writer handoff --id ID --body-file BODY`
after writer admission and evidence-plan registration. It preserves the body and
appends concrete writer/run-bound ledger instructions, saving an immutable prompt
receipt before printing. Use its complete output for initial work, finalization,
rework and follow-ups. Generation does not execute checks, intercept every host
spawn, prevent every bare check or certify delivery. Regenerate when bindings
change. These changes apply after installing this release; existing pinned runs
retain their original runtime.

## Bounded rework recovery

New runs begin with two rework returns. Exhaustion after a needed further return
persists a read-only Architect reassessment; at most two automatic Architect
authorizations add two returns each, for six default returns total across review
corrections. A genuine review correction does not replenish rework credits, and
its independent six-correction limit remains in force. Decline, no useful route,
or the automatic cap records the bounded recovery outcome. That outcome does
not prohibit ordinary scoped implementation when requirements and authority
are valid. The runtime records implementation continuity without replenishing
credits or erasing the reassessment history.

The supervisor retrieves a pending packet with `rework-recovery packet`, submits
the approved Architect's closed response with `rework-recovery decide --file
FILE`, and handles explicit user continuation only with a bound
`rework-recovery continue --file FILE` request. Each distinct accepted user
request grants exactly two credits; replay returns its original receipt. These
commands govern the bounded recovery protocol, not a routine developer approval
for continuing implementation. Tooling and incomplete checks remain operational
issues or verification debt; only material specification or engineering choices
require a developer decision. Real permission, identity, ownership and secret
protections remain enforced. An updated
installation does not migrate older runs or change their immutable runtime
issuer; those runs retain their original command set and evidence.

## Controlled benchmark storage

`benchmark_verify.py` allocates each cohort's disposable source copy, private
home and pytest temporary files through managed verification storage. Reports,
logs, probes, source bindings and ownership journals remain outside disposable
allocations. The report's `storage_cleanup_path` points to a separate cleanup
receipt; report bytes remain stable when cleanup completes.

Successful tests alone do not authorize deletion. Failed runs, changed sources
and unproven descendant settlement retain their allocations. Group-only process
cleanup remains unproven for this purpose. Qualified settlement and durable
publication use the existing storage finalizer. These allocations make benchmark
scratch attributable; they do not adopt historical directories or the active
native build cache. The storage warning and admission free-space reserve are
not a live-operation quota.
