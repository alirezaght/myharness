# Install and maintain AL

`alctl` manages one registered target at a time or enumerates already enrolled
targets for `update --all`. It does not search the home directory. A global
target stores Codex and Claude resources under the selected home directory; a
repository target stores them inside that repository and writes an `al.lock.json`.

## Registry location

By default, `alctl` uses `~/.local/state/al-distribution/registry` as the
existing distribution ownership registry. Its installer records are stored in
`<registry-root>/installer/`. `AL_DISTRIBUTION_STATE=/path/to/registry` and
`--state-root /path/to/registry` both select that registry root directly;
an explicit `--state-root` takes precedence over the environment value. Neither
option causes `alctl` to append another `registry` directory. `--home` controls
only the global target and does not change registry placement.

Legacy ownership records are shown as version unknown and remain read-only until
you explicitly review and adopt them. `alctl` does not migrate records or infer
ownership from matching file bytes.

## Bootstrap the CLI

From a reviewed checkout, install an independent CLI snapshot and launcher:

```sh
python3 al/alctl.py bootstrap
alctl --help
```

The default launcher is `~/.local/bin/alctl`; add that directory to `PATH` if
needed. The snapshot contains the trusted CLI, installer modules, release
layout, and existing distribution engine modules. Its receipt records a content
digest and development provenance. It keeps working if the checkout moves or is
removed. Re-running bootstrap verifies the snapshot and refuses modified
snapshots or unrelated launcher files.

Bootstrap installs only the CLI. It does not install AL into Codex or Claude.
For isolated testing, pass `--source-root`, `--destination`, and `--state-root`.

Phase 3 harness execution requires a protocol-capable installed package. Both
platform packages ship `al-execution-v1`, the task/admission and scoped-decision
commands, and the role instructions that report actual lifecycle facts. A
source checkout does not upgrade an installed runtime. Validate and install a
reviewed candidate only into an isolated project target for acceptance; do not
replace a global package while its AL runtime is active. An incompatible package
or missing role resource remains an explicit launch error.

## Install from a trusted release source

Choose a local Git checkout or credential-free HTTPS source whose tags you
trust. The installer accepts numeric stable tags only. It reads Git objects
without checking out or executing fetched files, validates the release layout,
and records the selected source identity and tag object.

```sh
alctl install --global --source ~/src/myharness
alctl install --repo ~/Documents/project --source ~/src/myharness --version 1.2.0
```

The default source subdirectory is `al`. The first command selects the newest
numeric stable tag and follows later releases. Supplying `--version` defaults
that installation to pinned policy. Use `--platform codex`, `--platform
claude`, or `--platform codex,claude` to select platforms; both are selected by
default. A project install requires an existing Git repository and creates a
lockfile even when its policy follows future updates.

An installation with a conflicting unmanaged file, unfamiliar instructions,
unsafe destination, unsupported configuration syntax, or changed owned file
stops for review. It will not infer ownership merely because existing bytes
match the release. Keep reviewed JSON artifacts outside the target repository
and pass them explicitly with `--reviews` or `--adopt` when the diagnostic
requires them. The CLI's refusal includes the affected path or key. Reviews
must bind the exact current instruction paths and hashes; configuration reviews
must state exact before and after values. Adoption artifacts must include the
prior-managed package provenance expected by the existing distribution engine
and explicit resource fingerprints where applicable.

An instruction review uses the exact paths and SHA-256 values currently found:

```json
{
  "instructions": {
    "codex-global": {
      "reviewed": true,
      "reason": "Reviewed the AL installation instructions for this target.",
      "instructions": [
        {"path": "/home/me/.codex/skills/al/AGENTS.md", "sha256": "<current-sha256>"}
      ]
    }
  }
}
```

Use every path and hash reported by the refusal. A review for an empty
instruction set is unnecessary. Configuration review entries, when required,
belong in a top-level `config` object and must include the exact dotted key,
`before`, and `after` states, each expressed as `{ "present": true,
"value": ... }` or `{ "present": false }`.

For legacy package ownership, the reviewed `--adopt` JSON has a `schema_version`
of `1` and a `targets` array. Each package row identifies its absolute package
`target`, `platform`, stable `identity`, review `reason`, and `reviewed: true`.
`source_manifest` and `inventory` are references shaped as `{ "path":
"/external/file.json", "sha256": "<hash-of-that-file>" }`. The referenced
source manifest maps package-relative paths to baseline SHA-256 hashes. The
referenced inventory lists each absolute target and platform with hashes for
the files observed there. The row's `files` object lists only files whose
ownership is justified; each entry records `sha256`, `mode`, and
`ownership_source`. Any file differing from its baseline also needs an explicit
`local_difference_reason`. Build these artifacts from preserved baseline
manifests and a reviewed target inventory; do not create a baseline by treating
the current target as trusted ownership. The existing [distribution guide](codex/skills/al/reference/distribution.md)
describes the engine's provenance checks and transaction boundaries.

The `resources` object may separately adopt a role or lockfile path. Each entry
must bind the absolute path to its current fingerprint, for example:
`{"reviewed": true, "reason": "Reviewed this existing role",
"state": {"sha256": "<current-sha256>", "mode": 420}}`. Existing target
configuration is still merged by owned keys and unrelated values remain intact.

## List, update, and rollback

```sh
alctl list
alctl update --all
alctl update --global
alctl update --repo ~/Documents/project --version 1.3.0
alctl rollback --previous --global
alctl rollback --previous --repo ~/Documents/project
```

`list` shows registered installations and legacy ownership records as version
unknown. Legacy records are visible but are not enrolled as updater targets.
`update --all` uses installer records only, preserves each target's selected
platforms, source, and policy, and reports each result. A failure or active-run
deferral in one target remains visible and makes the aggregate command fail.
Pinned targets are reported as pinned. A selected target can change its pinned
version explicitly with `--version`; it stays pinned unless `--policy follow` is
also supplied. Rollback is a new guarded operation to the previous completed
release and advances package ownership through the existing engine.

Updates stop when installed files, role files, owned configuration keys,
project lockfiles, backups, or transaction records have drifted. Edit unrelated
configuration freely; owned values are tracked separately and unrelated keys
are preserved. An interrupted transaction remains pending for diagnosis and
cannot be retried or rolled back by the ordinary commands.

An enrolled global target can explicitly install an exactly reviewed documentation
correction while retaining well-formed resumable histories:

```sh
alctl update --global --version 0.1.7 --documentation-only --reviews /absolute/reviews.json
```

This mode requires a stable numeric tag and preserves the enrolled source,
subdirectory, platforms, update policy and configuration. It rejects installation,
rollback, `--all`, adoption, policy changes and status-line changes. Normal updates
still require terminal histories. This mode reports retained activity honestly;
doctor may continue to report those histories as blockers. Malformed runtime
records, unsettled writers/evidence and ambiguous protected origins still refuse.
No existing runtime state, pin or immutable snapshot is rewritten.

The existing review JSON must contain a `documentation_update` object:

```json
{
  "documentation_update": {
    "schema_version": 1,
    "reviewed": true,
    "reason": "Inspected this exact correction against the existing platform contract",
    "old_release_digest": "<installed manifest digest>",
    "new_release_digest": "<candidate manifest digest>",
    "resources": {
      "claude/skills/al/SKILL.md": {"before": "<sha256>", "after": "<sha256>"}
    },
    "instruction_review": {"path": "/absolute/instruction-review.txt", "sha256": "<sha256>"},
    "user_authorization": {"path": "/absolute/authorization.txt", "sha256": "<sha256>"}
  }
}
```

The resource map must exactly match every changed payload resource. Provenance
files must exist and match their recorded hashes at both transaction barriers.
These artifacts record inspected instructions and actual operator authorization;
a filename or hash alone does not establish semantic equivalence or authorization.
Existing applicable instruction/configuration reviews remain required.

The fixed resource allowlist is Claude `SKILL.md` and the two platforms'
`reference/handoffs.md`. All resource membership, kinds, platform classifications
and modes must remain identical, and every other byte must match. Claude Markdown
role definitions are excluded. The two `reference/parity-manifest.json` files may
change only their schema-1 `platform_adapters.reference_rationales["handoffs.md"]`
string, accompanied by an exactly reviewed handoffs change. All inventories and
other parity fields must compare structurally equal; unknown digest/schema
extensions refuse. The journal records the compatibility receipt, changed hashes,
unchanged resource-set digest, release commits/digests, review provenance and
observed activity. Compatibility and preimages are checked before apply and
installed postimages are checked before publication. Ordinary ownership, cache,
tag, smoke, transaction and pending-recovery checks remain in force.

## Exact reviewed 0.1.8 runtime payload update

The separate release-specific operator mode replaces future global payload files
while preserving independently protected historical runtimes:

```sh
alctl update --global --version 0.1.8 --reviewed-runtime-update --reviews /absolute/update-reviews.json
```

Run this through the genuine owned managed launcher using its enrolled registry.
It supports only the exact reviewed 0.1.7 → 0.1.8 tag objects, commits and resource
fingerprints compiled into the installer. The fixed delta consists of seven
runtime script paths and the workflow/parity documents per platform; only
`al_writer_progress.py` may be added. Every other resource must match the reviewed
unchanged inventory. Arbitrary future code cannot be admitted by supplying new
hashes. Source/subdirectory, global target/platforms, policy, configuration,
status-line, roles and retirement/lock actions must remain unchanged. Install,
rollback, repo/batch targets, adoption, implicit versions and mixed modes refuse.
The ordinary and documentation-only guards retain their existing behavior.

The reviews JSON retains applicable `instructions` reviews and adds a closed
`reviewed_runtime_update` object containing exactly these fields:

```json
{
  "schema_version": 1,
  "kind": "reviewed-runtime-update-v1",
  "reviewed": true,
  "reason": "Exact inspected runtime correction and accepted future-payload scope",
  "target": "<complete enrolled target object>",
  "source": "<complete enrolled source identity/subdirectory object>",
  "old_release": "<old release_identity object>",
  "new_release": "<new release_identity object>",
  "resources": "<exact APPROVED.resources map of before/after fingerprints>",
  "independent_review": {"path": "/absolute/independent-review.json", "sha256": "107715fb8e6fa6c7b1c189bd1605503af2e33076ad006476b68397d86d1798b1"},
  "instruction_review": {"path": "/absolute/instruction-review.json", "sha256": "<sha256>"},
  "operator_authorization": {"path": "/absolute/operator-authorization.json", "sha256": "<sha256>"},
  "accepted_limits": [
    "future-global-payload-only",
    "historical-pins-and-state-unchanged",
    "reserved-initialization-settlement-unknown",
    "no-execution-or-approval-authority",
    "bootstrap-selection-is-a-separate-publication",
    "partial-failure-requires-manual-reconciliation"
  ]
}
```

The quoted object/map placeholders above must be actual complete JSON objects.
`release_identity` comprises version, tag, tag_object, commit, digest,
source_identity and subdir. Fingerprints contain sha256, mode, platform and kind;
the new module's before fingerprint is null. The whole review file and each
referenced artifact are hash-bound and re-read at installation barriers. The
independent review certificate binds the inspected executable change; instruction
and operator artifacts must record their actual provenance. No new run approval
or generic execution authorization is created by this review.

The mode independently enumerates protected index entries and all discovered
histories, including private session directories and durable epochs. Only typed
terminal-workflow blockers may coexist with the replacement. It verifies matching
protected state origins and complete immutable pin closures/interpreter profiles.
Missing, foreign, corrupt, aliased or ambiguous contexts and unresolved writers,
running/interrupted evidence or uncertain owned cleanup refuse. RESERVED records
remain initialization attempts with settlement unknown; the existing durable
reserved/missing-state refusal remains. No history, binding, pin, old cache or old
snapshot is moved, rewritten or relabeled. Activity remains visible in the result
and doctor may still report nonterminal histories.

Supported SUPERSEDED entries are assessed only as protected bookkeeping without
a run capability. The mode correlates the archived predecessor, immediate ACTIVE
successor, selector, activation/preparation records and referenced metadata. It
does not call the sealed Harness bridge or assess current SQLite epoch or approval
authority; the receipt labels those authorities `NOT_ASSESSED`. The normal run,
resume and current-authority guards still apply whenever a run is used. Missing,
changed or unsupported successor chains refuse the update.

The journal retains before/apply/platform/completion observations and exact review,
source, profile, pin and state digests. New or changed evidence at a barrier
refuses continuation/completion; a partial failure remains pending for ordinary
manual reconciliation. No automatic rollback is promised.

Global payload publication and managed coordinator bootstrap are separate actions.
Existing runs keep their immutable pins. New runs select payloads from the trusted
coordinator snapshot, so the operator must separately publish and verify the
intended reviewed bootstrap snapshot through the existing owned bootstrap path.
This command does not claim an atomic switch across those publications.

## Explicit operator update with running or unknown consumers

An operator who has authorized disruption may replace an enrolled global package
while runs are active or historical metadata cannot be fully inspected:

```sh
ALCTL=(/absolute/path/to/owned/alctl)
"${ALCTL[@]}" update --global --version 0.1.9 --allow-running --reviews /absolute/operator-reviews.json
```

Use the actual published numeric version and the genuine managed launcher with
its enrolled registry. This mode preserves the selected source/subdirectory,
platforms, policy, host configuration and status line. It rejects installation,
rollback, repository/batch targets, adoption, source overrides, implicit latest,
policy/configuration changes and combinations with the other special modes.
The ordinary update guard is unchanged.
The explicit mode refuses direct source invocation and a different `--state-root`:
the genuine isolated managed snapshot selects its registry. Installation records,
source observations, ownership records, role sentinels and the actual held
coordination-lock descriptor must remain owned, regular, non-symlinked and free
of hardlink aliases. This does not require or create a runtime pin directory.

Activity is a bounded metadata warning in this explicit mode. Active writers,
unsettled evidence, oversized/unreadable/unsupported histories and discovery gaps
are reported without claiming process settlement, complete census, legacy
independence, pin integrity, current epoch authority or AL approval. Existing
states, pins, indexes and immutable snapshots are not migrated, archived, deleted
or rewritten. Do not reuse this warning receipt as run/resume/writer/check authority.

The review JSON contains one closed `global_package_update` object, plus existing
applicable `instructions`/`config` reviews. Its schema is version 1, kind
`operator-global-package-update-v1`, operation `global-update-allow-running`.
It binds the exact enrolled `target`/`source`, complete `old_release`/`new_release`
identities (version, tag, tag object, commit, digest, source identity, subdirectory),
`manifests` references for both exact cached manifest files, the complete computed
`resources` delta (before/after SHA256, mode, platform, kind; null for additions or
removals), `unchanged_resource_set_sha256`, and versioned `projection` digests for
`authority_sha256` and `configuration_sha256`. All values must match the actual
resolved caches and prepared transaction; supplying hashes cannot change its plan.

The object also requires substantive `reason` and `provenance`, hash-bound absolute
`operator_authorization`, `instruction_review` and `source_review` references,
and the exact `accepted_limits` list:

```json
["running-or-unknown-consumers-may-be-disrupted",
 "legacy-independence-and-settlement-not-assessed",
 "no-execution-or-approval-authority", "bootstrap-is-a-separate-publication",
 "partial-visibility-and-manual-reconciliation"]
```

Each reference is exactly `{ "path": "/absolute/file", "sha256": "<SHA256>" }`.
The independent source-review JSON has exactly `schema_version: 1`, kind
`operator-global-package-source-review-v1`, `reviewed: true`, the same
`old_release`, `new_release`, complete `resources` and `projection`, plus substantive
`reviewer` and `reason`. It must reflect an actual independent review of the code
and instruction delta. Authorization provenance records the real operator request;
it must not invent exact-hash user consent. These are operator review artifacts,
not cryptographic proof of a reviewer or an agent authority token. The whole
reviews file and every reference are re-read and hash-checked at all barriers.

Codex role files preserve the entire parsed TOML object except the string
`developer_instructions` body. Claude role and both SKILL frontmatters preserve
all bytes through the closing delimiter. Role names, paths, modes and retirement
remain identical. Actual model policies, named hook resources and their local
Python implementation dependencies remain unchanged. Reviewed instruction bodies
and supported package resources may change only within the computed reviewed
delta. Missing/invalid envelopes, host wiring changes, unsafe destinations,
symlinks/hardlink aliases, collisions, drift, corrupt caches/tags/registry,
changed ownership generations and pending journals remain hard refusals.

Preparation, pre-apply, each platform and completed publication recheck reviews,
source, authority/config projections, plan identity and appropriate resource
preimages/postimages. Activity may legitimately progress between these barriers.
Package and role file replacement uses the existing recoverable multi-file
transaction: legacy readers may see partial/intermediate files. Failure retains
the pending journal/backups for manual reconciliation; do not retry, claim an
automatic rollback or report completion. Bootstrap is a separate explicit supported
publication. Existing protected pins retain their original bytes; unknown legacy
invocations may still be disrupted by this mutable package replacement.

## Inspect and recover

```sh
alctl doctor
alctl doctor --global
alctl doctor --repo ~/Documents/project
```

Doctor is read-only. It checks prerequisites, release cache, installed
ownership, configuration and lockfile state, operation journal links, and
runtime activity in known bounded roots. It does not create a state directory,
fetch a source, repair cache data, or modify an installation. A nonzero result
includes the problem paths and next diagnostic context.

Active-run protection covers registered and invoking repositories, stored and
explicit runtime roots, and `AL_RUNTIME_DIR` when set. It does not scan the
entire home directory; an unregistered legacy run may be outside coverage, and
older runtime starts do not share the installer lock. A blocker, malformed
session, or incomplete operation causes a refusal or deferral. Review the
reported session/journal and backups before any manual recovery. Do not remove
pending metadata or restore backups by hand unless the complete journal proves
which exact preimage should be restored.

### Stale run bindings

A protected run binding whose repository root no longer exists (a deleted
worktree or temporary repository) can never resume. Doctor lists it under
`stale_runs` with a warning instead of failing, and it no longer blocks an
update. Archive such bindings with:

```sh
alctl prune-stale-runs          # dry run: lists stale bindings and their records
alctl prune-stale-runs --apply  # moves them into runtime-archive/<timestamp>/
```

Only bindings whose repository root path is missing qualify. `--apply` moves the
binding, its initial successor, successor fence, initialization records and
durable runtime directory into `runtime-archive/<timestamp>/` beside the registry
and writes a `manifest.json` listing each `source` and `archived` path. Nothing
is deleted; restore by moving each archived path back to its source. A binding
whose lock is held is skipped.

### Resident service

`alctl run` starts a resident AL service per repository and pinned runtime on first use;
later commands of the run are served by it with byte-identical results and otherwise run in
process. `alctl service status` and `alctl service stop` (optionally `--repository R`) show
and stop the services of this user; `AL_SERVICE=0` disables it and `AL_SERVICE_IDLE_SECONDS`
sets its idle timeout. The thin client lives in the launcher, so a new launcher (re-bootstrap
or update) is needed to use it. See [performance.md](codex/skills/al/reference/performance.md#resident-service).

### Pin verification cache

An in-process `alctl run` command checks its pin's manifest every time and re-hashes the
pin's files only when they changed. After a full verification passes, the launcher
writes `al-run-pins/pin-verification/<pin>.json` (0600 in a 0700 directory, written
atomically). The record holds the pin identity, manifest digest, every file's content hash
and the device, inode, mode, owner, link count, size, mtime and ctime of every file and
directory in the pin. If any of those differs, or the record is malformed, foreign or has
the wrong permissions, the launcher runs the full verification again. A failed
verification is never recorded. install, update, rollback, doctor and every caller other than
`alctl run` always verify in full. Deleting the directory only clears the cache.

### Existing run pins and successor guidance

Installing a release and bootstrapping a coordinator do not replace an existing
run's immutable pin. `alctl runtime-successor origin --repository REPOSITORY
--platform PLATFORM --session SESSION` inspects its protected origin; it does
not migrate it. Standalone `runtime-successor inputs` reports
`UNSUPPORTED_RUNTIME_SUCCESSOR_MODE` because that protocol requires a real
Harness integration binding and handoff.

For an existing Harness execution, use the trusted operator's supported
`harness execution runtime-prepare`, `runtime-inspect` and `runtime-activate`
flow, with its actual control store, current epoch/generation, exact request and
preparation digest, settled operations and supported containment. Preparation
has no product authority; activation fences the predecessor and never resumes
the native session automatically. See the complete array-based commands and
prerequisites in [distribution.md](codex/skills/al/reference/distribution.md#existing-runtime-pins)
([Claude copy](claude/skills/al/reference/distribution.md#existing-runtime-pins)).

Standalone users can inspect the old origin and, after actual settlement, start
a genuinely fresh run from the preserved accepted package and requirements.
Keep old history and artifacts as references and revalidate all evidence,
planning, contribution and approval credit. This is a new run, not an in-place
upgrade. See [implementation-continuity.md](codex/skills/al/reference/implementation-continuity.md#inspect-a-pin-and-choose-the-supported-route)
([Claude copy](claude/skills/al/reference/implementation-continuity.md#inspect-a-pin-and-choose-the-supported-route)).
No supported route edits a retained pin/state/index or invents standalone
successor authority, coordinator tokens or transferred PASS results.

## Releasing

Ordinary installation uses fixed structural smoke and integrity checks. Release
verification has separate workspace, exact-tag payload, and complete-suite
commands:

```sh
python3 al/release_verify.py --check-layout
python3 al/release_verify.py --check-release --source ~/src/myharness --version 1.2.0
python3 al/release_verify.py --full
python3 al/release_verify.py --full --version 1.2.0
```

For routine development verification, use `uv run python al/verify_changed.py`
instead. It runs only the tests that your change affects, in parallel, and
makes no release claim. See [README.md](README.md#verifying-a-change).

`--check-layout` audits this checkout's workspace layout and makes no release
claim. `--check-release` requires an explicit stable version and an existing
local Git source; it resolves committed tag bytes, validates the manifest and
cache in a temporary directory, and never uses dirty working-copy bytes.
`--full` runs the complete installer suite and both complete platform test
suites. Without release context it reports development provenance, including
the checkout's available commit and dirty state. An explicit `--version` binds
the full run to that local tag. In CI, `refs/tags/<tag>` selects that exact tag
and requires its commit to match both `HEAD` and the full `GITHUB_SHA`. Tagged
runs require a clean checkout before and after the suites; a changed tag,
checkout, or file prevents a release result. A manual dispatch on a branch is a
development check; a manual dispatch on a tag uses the tag binding.

The release workflow performs `--full` on version tags and manual dispatch. It
only verifies source and does not publish files or create tags. Create and
publish a tag through the repository's normal reviewed release process. A dirty
development CLI bootstrap is never a tagged runtime release.
