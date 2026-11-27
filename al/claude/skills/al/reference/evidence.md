# Evidence supersession and isolated setup

Managed verification is noninteractive by default: ordinary evidence, gates, setup and
version checks, remote checks, and managed-storage/recipe children receive EOF on stdin
through a pipe whose writer is closed before launch. This does not set `CI`, add repository-specific flags, or reclassify
command failures. An explicit standalone stdin pipe or file keeps its supplied input;
input-descriptor transport through Harness is not qualified by this contract. Generic
interactive/native process launchers retain their existing stdin behavior.

Managed Harness checks negotiate the exact execution policy before allocating outputs or starting a child. Unsupported backends or explicit remote input are refused without fallback. New backends retain DEVNULL for historical clients that omit the policy; managed clients require the matching start acknowledgement.

The versioned execution policy is separate from environment inputs. It binds action-cache
keys and ledger fingerprints, including environment-only/preflight checks. Evidence from
an older policy must be rerun even with the action cache disabled; immutable old pins and
historical evidence are not rewritten.

Register a check with AL options before the literal `--` separator and the
program plus its arguments after it:

```sh
"${STATE[@]}" evidence plan --area unit --scope src,tests -- python3 -m pytest -q tests
"${STATE[@]}" evidence run --area unit
"${STATE[@]}" evidence run --area lint --kind lint --scope src -- python3 -m ruff check src
```

`plan` requires a nonempty command. `run --area unit` reuses the registered
command; an ad-hoc `run` supplies its own scope and command after `--`. A bare
trailing `--` is an error for either action. Command flags after the separator
belong to the check, and each shell argument is preserved literally; no shell
command string is inferred or split.

A rerun of an area reuses its last PASS only while the check's inputs are
unchanged, and its scope fingerprint is not enough to show that. A check
also reads files it does not declare: a browser spec runs against a build of
`src/`, and a linter reads its config. So each run records an inputs
fingerprint covering every repository file (tracked, or untracked and not
ignored) in the check's package, plus its scope. The package is the nearest
directory at or above the check's cwd that holds a project manifest
(`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml` and similar), or the
repository root. If any of those files changed, `evidence run` prints
`E<n> not reused` and runs the check again.
A change in another package keeps reuse. Validity is unchanged: a change outside
the scope still does not make the earlier PASS STALE, it only stops it being
reused. Declare cross-package dependencies in `--scope`, or pass `--fresh`.

`CI` is an automatic verification dependency, even when absent or omitted from
`--env`. Its presence and exact value are fingerprinted as hashes for ordinary
evidence currentness, action-cache identity and verification preparation. A
changed `CI` value invalidates evidence bound to the previous value. An existing
cache entry may still be reused when its recorded environment matches the
current inputs exactly; old policies that do not bind `CI` are stale. These
fingerprints never assign environment values.

`--env CI` declares an input; it does not set `CI` or broaden a managed execution
environment. Use an approved explicit command assignment, such as `--env CI=true`
(which records `env CI=true` in argv), when the check requires that value. The
literal assignment controls the child's value; an ambient `CI` change can still
conservatively invalidate its evidence. Other repository-specific environment
inputs require explicit `--env` declarations.

## Action cache (reuse across runs, tasks and worktrees)

When no PASS of this run can be reused, `evidence run` looks the check up in the
repository's action cache before executing it. The key hashes the argv, the cwd
relative to the repository, the kind, scope and sources, the declared `--env`
values (as digests) plus the environment policy, the tool identity (the resolved
executable and its content hash, plus the toolchain lockfiles such as `uv.lock` or
`pnpm-lock.yaml` from the repository root down to the cwd), and the input tree: the
git tree of the check's scope plus its package (the rule above), or of the whole
repository when the package is the root. The tree comes from the working-copy
snapshot (`al_snapshot.input_tree`, a private per-worktree index), so untracked
files count, every tracked file counts wherever it lives (`dist/`, `build/`,
`node_modules/` included), and untracked files in AL's generated and cache
directories do not; inputs holding a nested repository or a symlink out of the
repository are never cached. Any change
to any of these is a miss.

A hit records an ordinary new entry with the original log, status and counts,
fingerprinted against today's tree, and prints
`reused from run <run> (<entry>) (action cache, inputs unchanged)`. `evidence
status` and the review packet (`reused_results`) show the run where it originally
executed. A cached FAIL or EMPTY is reused too; INFRA, TORN, timed-out, interrupted
or cleanup-uncertain results are never stored, and neither is a run whose inputs
moved while it ran.

The store lives in the git common directory (`.git/al-action-cache`), so every
worktree of the repository shares it. Records are immutable and content-addressed,
written lock-free with an atomic rename and bounded by LRU (`AL_ACTION_CACHE_MAX`,
default 4096 records). `--fresh` executes and replaces the record, and so does running
an area again after its latest attempt in this run did not pass (a retry after a flake
or a timeout): only a new run reuses a cached FAIL.

A check that reads undeclared state (network, time, randomness) opts out with
`--non-hermetic` on `evidence plan` or `evidence run`; `--no-cache` skips the cache
for one run, and `AL_ACTION_CACHE=0` disables it entirely. Recipe checks and checks
with managed outputs are never cached.

## Writer checks during parallel writers

`evidence run ... --writer <id>` runs a writer's own check between its edits. When the
check's scope also covers paths of another ACTIVE writer (whose ownership is disjoint), the
run still proceeds and is recorded as **provisional**: `evidence status` shows it as
`PROVISIONAL — needs revalidation after writer B finishes` and it is not evidence yet.
When B finishes (or is reconciled), `writer finish` reports each provisional result it held:
the scope fingerprint decides, so the result becomes `VALID` if nothing the check reads
changed since it ran, or `STALE` (rerun the area) if B changed any of those inputs. An edit
during the run itself makes it `TORN`. When the files that changed during a run can be named, the note
names them: `check A modified tracked file uv.lock in its own scope during its run` when no other
AL check ran meanwhile (for example a `uv run` that re-locks), or the overlapping checks when some did.
Make such a command non-mutating (a `--frozen`/`--locked` option) and plan it again, or keep the change
deliberately. A sibling area whose PASS that write made stale shows `STALE — affected-by-A: ...` instead
of a generic stale reason, and `writer finish --evidence auto` reports it as `AFFECTED-BY-A` and names
the mutating check; restoring the file revalidates the sibling, since fingerprints are content-based. While B is ACTIVE or UNRESOLVED the result stays
provisional, so review, approval and `needs` prerequisites never accept it. A check without
`--writer`, a shared-state command, or a barrier phase is still refused while it overlaps an
unresolved writer.

`evidence supersede --id EOLD --replaced-by ENEW --reason TEXT` appends an immutable audit artifact. Both entries and their logs remain unchanged, including the original FAIL. The replacement must be a newer original execution, a current VALID PASS of the same declared obligation and current plan. A later failed/running attempt wins. Changed command semantics require separate supported replanning; free text cannot assert equivalence. The refusal names the supported path: a replanned area's earlier runs no longer count (`PLAN_CHANGED`), and a misregistered area is withdrawn for its corrected one with `evidence retire --area OLD --replaced-by NEW --reason TEXT`. Corrupt logs, unresolved processes, foreign runs and conflicting replay refuse without changing state. Exact replay is idempotent while its binding is current.

This records why a current rerun replaces obsolete evidence; it does not resurrect old receipts, clear review findings or retire gates. Evidence status and inspection expose the relation. Area/repro readiness still uses the current command and latest-attempt ledger rules. `evidence retire` remains a distinct mistaken-plan operation.

`evidence status` marks a FAIL, INFRA or TORN entry `superseded by later PASS E<n> (same command and scope)` when a newer run of the identical command and scope is the area's current VALID PASS, for example a timeout on a busy host that passed on an immediate re-run. The mark is display only; the latest-attempt rule already makes that PASS the area's evidence, and `evidence supersede` still records an audited reason.

### Sharded areas

A suite that CI runs in shards, or that cannot run as one local process, is planned as shards:

```sh
"${STATE[@]}" evidence plan --area e2e --shards 4 --scope apps/web -- npx playwright test --shard={shard}/{shards}
"${STATE[@]}" evidence run --area e2e.shard-1-of-4
```

`--shards N` plans N ordinary checks `AREA.shard-I-of-N`, replacing `{shard}` with I and `{shards}` with N in the command; the command must contain `{shard}`. Each shard is run, reused, superseded and invalidated like any planned check. The area is evidenced by the set of its current shard PASSes: `evidence require --areas e2e` and `--needs e2e` expand to every shard, `evidence status` prints `sharded area e2e · K/N shards VALID`, `evidence run --area e2e` names the shard commands, and `evidence retire --area e2e` retires all shards. Planning the area again (sharded or not) replaces its previous shard set; earlier runs stay in the ledger.

Package-backed hooks use source-selected locked setup in disposable checkout storage. Setup records command, input hashes, executable identity, timing, outcome and cleanup before the hook. Missing tools, failed install and unsafe changed inputs are setup INFRA; code has not passed. Hooks then execute against prepared dependencies. Local environment paths and caches stay inside owned temporary storage; no global installs, generated locks or working-tree dependency links are used. General checkout symlink guards remain strict; only the owned venv link to the independently bound running interpreter is materialized as a copy.

Diagnostic status commands can exit zero while displaying FAIL or BLOCKED. Execution/refusal commands return nonzero when they do not succeed. Actual provider pending/manual/canceled results are recorded as unsuccessful verification, never code PASS or an invented provider test failure.

## Derive evidence recipes from CI sources

Before registering a lint or type check, inspect the actual CI job and its local
wrappers, then record the source paths and current hashes that define the recipe.
Preserve the exact tool target and flags, configuration, working directory, setup
prerequisites, environment values, and whether each wrapper step is required or
advisory. A CI wrapper such as `fc_step` executes the supplied command; it does
not imply additional targets. Do not infer a broader command from a job name.

Behavior observations also require the asserting test file to be inside the
scope of selected executed evidence, alongside the implementation and dependency
inputs. Passing a test path as a command argument does not add it to `--scope`.
For example, `--scope src,tests -- python3 -m pytest -q tests` is suitable when
those directories cover the actual inputs and assertion files; it is an example,
not an automatic or universal scope. Add other relevant inputs explicitly.

If an assertion is outside scope, retain the earlier execution and its recorded
scope. Register the corrected scope with `evidence plan`, execute it with
`evidence run --area AREA --fresh`, then resubmit the observation with the new
evidence ID. Editing an observation or changing a plan cannot expand an earlier
execution's scope, manufacture a rerun, or retrospectively credit its PASS.
The behavior packet repeats this guidance outside submitted observation objects.

Keep the command target separate from `--scope`: target arguments are passed to
the tool, while scope lists every source, fixture, script, configuration,
dependency manifest, and imported dependency that can affect reuse. Adding a
file to scope must not add it to the tool command. A changed-file check belongs
in its own named area. A component PASS does not establish that the complete CI
job passed.

`--env NAME` records and fingerprints the observed environment dependency; it
does not assign `NAME` in the check process. `--env NAME=VALUE` assigns an
approved, non-secret literal: AL registers it as an `env NAME=VALUE ... PROGRAM`
argv prefix. `--env` is repeatable and every occurrence is applied; one
assignment per occurrence, so the value may contain commas. `evidence plan` prints
the resulting command and its environment (`env sets ...; depends on ...`).
`evidence run --area` with an `--env` that differs from the plan is refused, not
ignored. Assignments cannot be combined with `--storage-file`; write the
`env NAME=value PROGRAM ...` form in the command instead. Never copy ambient secrets into argv,
artifacts, or logs. Relative paths in argv resolve from the registered check
cwd.

The evidence runner does not create an isolated checkout or run package setup.
Prepare dependencies only in an already authorized, owned environment and
preserve the CI setup command and lock inputs. Generic `uv sync --locked` is not
proof of equivalence to a CI command with different extras or frozen semantics.
If the managed profile cannot represent the source recipe or setup, report that
verification gap; do not bypass the profile or execute a guessed substitute.

A managed evidence run sets `TMPDIR`, `TMP` and `TEMP` to a fresh private
directory directly under `/tmp` (or `/var/tmp`), named `al-<random>`, mode 0700
and owned by the user; it is removed once the check's process group has
settled. It stays short because Unix-domain socket paths are limited (104 bytes
on macOS, 108 on Linux) and tools such as Python multiprocessing (`black`,
`pytest-xdist`) bind sockets under `TMPDIR`. Declared scratch, outputs and the
uv cache stay inside the managed allocation. A check that sets its own
`env TMPDIR=...` keeps it. A check dispatched through a Harness execution gets
the same kind of directory from Harness, named `/tmp/h-<random>`. Its sandbox may
write, and bind or connect Unix sockets, only inside that directory, never
elsewhere in `/tmp`.

### Source-backed example: user-listing service

This example is specific to the inspected Amber CI sources, not a repository
default. Its source inventory at review time was:

- `ci/scripts/fast-check-python.sh` — SHA-256
  `62c06afb2ed713572d3966eba973bd36e305d619fa809cac1c0a3db965ef8842`
- `ci/scripts/fast-check-lib.sh` — SHA-256
  `b5f436519a47b255e55bd1372f672b770387916badb1f0b41bc16ec31c80f0c0`
- `packages/tooling/ruff/ruff.toml` — SHA-256
  `d0d9502e868cddc616ac1a3d1535e61cc8e72f4c6a2602a0211d862831d4a720`
- `packages/tooling/mypy/mypy.ini` — SHA-256
  `36ddb05b81e5b0d81210cd54d32b172fa76cbb9f8e0a52fc25dcd5382f9585c0`
- `services/user-listing-service/pyproject.toml` — SHA-256
  `1401dcfbcc0c5b662be9380fd5fbc7cc795964174fc8a2d0aed9ebbe2e2f70d2`
- `services/user-listing-service/uv.lock` — SHA-256
  `2b442816dd67ffdf411b55db3db657f54f3a6f4388e59e0530cf95f1be905222`
- `packages/contracts/python/pyproject.toml` — SHA-256
  `98306e400ffdf1cf0c30e8040d4cbc76ef51179658864e1c75e8fba4b41511ae`

The service job runs from `services/user-listing-service`. In an authorized
owned preparation environment, its required setup is `uv sync --extra dev
--frozen` from that directory. That setup is not performed by ordinary
`evidence run`. For the registered commands below, the relative configuration
paths rebase the CI `${root}/packages/...` paths from this exact service cwd.

Lint area, with `src` as the tool target:

```sh
env UV_NO_SYNC=1 MYPYPATH=src:../../packages/pyshared/src:../../packages/contracts/python uv run ruff check src --config ../../packages/tooling/ruff/ruff.toml
```

Type-check area, with `src` as the tool target:

```sh
env UV_NO_SYNC=1 MYPYPATH=src:../../packages/pyshared/src:../../packages/contracts/python uv run mypy --config-file ../../packages/tooling/mypy/mypy.ini src
```

For either area, scope the CI scripts, the service source and manifests, the
governing service lock, the referenced tool configuration, and the imported
`packages/pyshared` and `packages/contracts/python` manifests and source. Discover
the actual governing lock and imported inputs for the service; do not substitute
a nearby package lock. The CI `fc_step` lint and type commands are required;
`fc_advisory_step` is advisory. Neither example proves the full `fast_check`
job or any actual lint/type result.

If an accidentally broad check fails, retain its failure and log. Record a
correctly scoped component area separately, then use `evidence retire` with a
reason to retire the mistaken plan. `evidence supersede` applies only when the
replacement satisfies its existing same-obligation and current-plan rules; it
cannot assert equivalence between changed command semantics.


### Task-scoped coverage policy (v2)

New repository discovery records global inventory diagnostics separately from task requirements.
A deferred diagnostic is retained with a sourced exclusion proof; it is neither a passing check
nor an execution `NOT_APPLICABLE` result. Root checks and literal runtime/build document
consumers remain required even when shared narrative documentation selects no service.

Discovery, planning admission, conventional verification, directory expansion and currency use
the same reconstructed relevance policy. Unknown scope and incomplete applicable CI remain
obligations. Recognizing a generated GitLab job does not supply an equivalent local execution.

Captured repository, rule and CI bytes, membership, absences and boundary metadata are validated
as one attempt. Only typed source drift permits one fresh retry. Permission, syntax and bound
failures remain explicit failures. Evidence still depends on applicable source bytes; unchanged
dependency semantics do not make stale execution evidence valid.

Retained v1 declarations retain their original interpretation. Current v2 admission requires
actual rediscovery/replanning; a policy transition is pathless and cannot be silently refreshed.
Scoped implementation continuity does not admit proof, review or approval.


### Static Go module coverage

Current V2 discovery composes conventional Python observations with captured Go
modules (`conventional` adapter v1), or declared contract units (`contract` v2).
Retained V1 declarations keep their historical Python/contract-v1 identities and
require supported rediscovery; they do not acquire Go module semantics by relabeling.

Go discovery reads bounded literal `go.mod`/`go.work` declarations and optional
`go.sum`/`go.work.sum` inputs, using the same no-follow snapshot and one-retry
capture protocol. Current and base local dependency edges select reverse module
consumers. Nested modules own their source separately. Missing/opaque workspace
members, escaped replacements, unsupported syntax and unowned Go source remain
named obligations. Unrelated module diagnostics remain visible globally while
document-only tasks use the normal source-backed relevance projection. Newly
changed Go modules are real task source and require current coverage.

A module needs a sourced full-module verification recipe. Complete recursive test,
vet or build recipes retain their actual purposes; vet/build do not establish
test coverage or test counts, and never discharge a separately required test job. `go test .` and `go test ./sub/...`
do not establish `go test ./...` coverage of the enclosing module; a parent module's
recursive check does not cross a nested module boundary. Vet/build remain distinct
required checks; every declared CI obligation still requires its own evidence. Partial selectors and masking flags cannot supply module proof.
Literal gotestsum `--` arguments and bounded Make target/prerequisite/include
chains are parsed as data, with their inputs bound to the recipe. Discovery never
runs Go, Make, containers, downloads or repository expansion code.

Missing local tools are execution INFRA, never evidence of success. Actual native
`go test` receipts may have unknown counts; retained exit/output evidence must stay
truthful. Container Go recipes can be identified without host Go, but their
metadata grants no local execution mapping: actual authorized container/remote
verification remains required. Existing setup, platform, ownership, containment,
planning, behavior and review guards continue to apply.

Direct GitLab scripts retain literal working-directory changes across lines;
GitHub run steps and Make recipe lines retain their separate shell boundaries.
Option-like targets and `cd` operands are unsupported. A versioned external
requirement is not a local source edge merely because another captured module
has the same identity. Explicit local replacements and workspace resolution
contexts establish local impact; shared workspace contexts cover all members,
including imports that do not need a separate module `require` directive.


Unsupported module directives preserve relevant reverse-consumer uncertainty;
a changed Go module cannot silently drop consumers whose manifests could not be
fully interpreted. Demonstrably unrelated documentation keeps the global record
and the existing bounded deferral.

The bounded Make subset rejects semantic special targets such as `.ONESHELL`
and quoted/computed include forms. Plain `make TARGET` maps `Makefile` only when
higher-priority `GNUmakefile`/`makefile` selections are absent or the latter is the
same physical case alias. Selection observations and higher-priority absences
participate in the authoritative snapshot; missing prerequisite files remain
unverified rather than empty content observations.

Local Go verification uses an explicitly qualified effective environment:
`GOENV=off`, absent/empty `GOFLAGS` and `GOWORK`, and (for Make recipes) no ambient
Make flags or hidden include files. Unsupported persisted/default Go configuration
or selectors create named verification debt. Discovery reads trusted fingerprints,
not user configuration files, and never rewrites the source environment. The
same fixed precondition is enforced again at actual execution before Go starts;
an unsupported context produces a fixed diagnostic and INFRA preflight outcome.
Container recipe observations remain unqualified and independent of host Go or
host environment settings, pending their separately authorized execution route.

When cleanup records denied process inspection (for example `/bin/ps: Operation not permitted`), the result remains `INFRA`. Request the host’s reviewed permission for process inspection; in Codex, `require_escalated` is a host execution option, not an AL CLI flag. Establish positive owned-process settlement and satisfy existing cleanup obligations, then retry the same protected evidence invocation with inspection available. Keep the original INFRA entry as history. A reaped leader, missing PID, reconciliation or successful check output alone does not prove descendant settlement.

## Testing standards

Apply these standards whenever authoring, changing, consolidating, or reviewing a test. Before changing a test, inspect the production path and understand the observable contract and risk it protects. Start from the specification and realistic regressions, not from the implementation's current shape.

- Assert observable behavior and meaningful outcomes: normal behavior, important edge cases, failure behavior, integration boundaries, security boundaries, and previously fixed regressions as relevant. A test should name a realistic regression it would catch. Test names and claimed coverage must be backed by substantive assertions.
- Avoid tests that only establish that code, configuration, files, keys, or static values exist. Keep such checks only when presence itself is an explicit shipped prompt, schema, package, or security contract, and assert the meaningful contract/content and scope. Avoid mirroring private implementation steps; test public/observable behavior. Ensure loops execute and assertions are non-vacuous. For mutation claims, capture a before-state and assert the after-state changed correctly. For an HTTP or read-only persistence claim, inspect the persisted before/after state or use the actual handler connection; a separate connection's aggregate `total_changes` is not evidence of what the handler persisted. Verify via a separate connection or process when relevant. Coordinate races with deterministic barriers/events, not sleeps. Make tests isolated and order-independent.
- Exercise meaningful inputs and dispatch/action effects, not merely that a helper was called. Prefer the least-mocked seam that proves the contract. Keep synthetic, static, browser, and native execution evidence distinct and describe what each actually establishes. Browser evidence also checks visual alignment, layout, focus, scrolling, and other UI/UX hiccups.
- Preserve tests protecting functionality, business logic, important edge cases, integrations, security, and fixed regressions. If value is uncertain, keep the test and document the concern. Never delete useful tests, weaken assertions, skip relevant failing tests, or change behavior merely to obtain green results.
- During an explicitly authorized test cleanup, review the suite holistically across unit, API/integration, and end-to-end coverage; parallelize independent review work only when useful. For every file, understand the production behavior first; classify duplicate, hollow, implementation-detail, and existence-only tests; document each removed, rewritten, retained, or flagged disposition and why. Remove only tests demonstrably incapable of meaningful protection. Consolidate overlap only after comparing fixtures, parameters, platforms, helpers, and assertions, and transfer every unique assertion into the surviving test. Strengthen weak tests when the underlying behavior matters. Do not alter production behavior during cleanup unless a test exposes a genuine defect.

### Standalone operator facts evidence

Acceptance for standalone facts uses the installed managed launcher and public Knowledge refresh.
Source-script runners and mocked facts clients remain useful for narrow unit decisions, but they do
not establish installed-route behavior. Compare operator results with the actual run-bound route on
the same committed and supported working-copy bytes. Inspect refresh persistence from a separate
connection to its disposable SQLite file. Assert source, index, refs, and protected run state around
operator calls, allowing only the reviewed private derived-analysis storage. Target execution
canaries are inert fixture programs; observe that their marker remains absent. Use deterministic
barriers for movement cases and record when a test exercises the capture seam rather than the full
public launcher. Pack admission counters distinguish selected-pack declared logical bytes from
returned/hash-verified payload bytes; neither measures Git's internal decompression or peak memory.

### Standalone operator facts evidence

Acceptance for standalone facts uses the installed managed launcher and public Knowledge refresh.
Source-script runners and mocked facts clients remain useful for narrow unit decisions, but they do
not establish installed-route behavior. Compare operator results with the actual run-bound route on
the same committed and supported working-copy bytes. Inspect refresh persistence from a separate
connection to its disposable SQLite file. Assert source, index, refs, and protected run state around
operator calls, allowing only the reviewed private derived-analysis storage. Target execution
canaries are inert fixture programs; observe that their marker remains absent. Use deterministic
barriers for movement cases and record when a test exercises the capture seam rather than the full
public launcher. Pack admission counters distinguish selected-pack declared logical bytes from
returned/hash-verified payload bytes; neither measures Git's internal decompression or peak memory.
