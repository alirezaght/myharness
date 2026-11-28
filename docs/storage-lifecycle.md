# Managed disposable storage

Harness storage management applies to scratch allocations created through the
managed lifecycle. It does not adopt existing directories by name or age.
Existing verification trees, native build targets, runtime pins, immutable
history, and unknown allocations remain outside automatic cleanup.

## What gets reclaimed

A fresh allocation is created under the private `HARNESS_HOME/managed-storage`
namespace and bound to its run or check reservation. A successful result is eligible for cleanup only after the owner and all
relevant descendants are settled on a supported, verified containment path,
required result accounting is complete, source and classification bindings
still match, and immutable evidence has been published outside scratch. An
empty process group and a reaped leader establish group completion only; they
do not prove that detached descendants are gone. Ordinary standalone and
macOS group-only runs therefore retain scratch with
`descendant_settlement_unproven`. The Linux PID-namespace route has no positive
qualification in this release. Cleanup records a separate receipt; it does not
change the evidence result.

Failed, skipped or otherwise incomplete test output, cancellation, lost
publication, process uncertainty, active consumers, and changed source or
classification retain the allocation. A settled process by itself is not proof
that its output can be discarded. Repeated cleanup is idempotent, while partial
cleanup is reported with a continuation state.

Managed evidence declarations separate disposable operands from durable
outputs. For example, a recipe may declare a pytest fixture directory and a
required XML report, then use exact reserved argv tokens such as
`@AL_SCRATCH:fixtures@` and `@AL_OUTPUT:junit@`. The report is copied to its
durable location and verified before successful scratch can be removed. Keep
logs, assignments, reports and other authoritative outputs outside scratch.
Legacy recipes without a declaration remain unmanaged unless the public AL
runner applies its default per-run operation scratch.

The normal authenticated `Task.prepare` path registers a source-bound
`harness-nonbrowser-v1` descriptor for applicable tasks and creates its named
public evidence plan through the Supervisor AL boundary. Repeated preparation
reuses the identical plan; conflicts remain pending for replanning. Unrelated
or unresolved task scopes do not receive a fabricated broad-check plan. This
prepares and plans the recipe; it does not execute it during dependency setup.
The recipe partitions work through ordinary evidence reservations and private
check handles. However, group-only completion does not authorize partition
cleanup, so macOS and standalone runs currently retain scratch as
`descendant_settlement_unproven`. Linux namespace-backed cleanup remains
unqualified. The source-defined monolithic `uv run pytest -m "not browser"`
CI jobs remain unchanged and required; their mixed-outcome allocation may be
retained. The eight sealed storage modules are `storage_filesystem.py`,
`storage_lifecycle.py`, `storage_inventory.py`, `storage_declarations.py`,
`verification_contracts.py`, `verification_harness.py`,
`verification_pytest.py`, and `verification_harness_child.py`, plus the inert
package initializer. This lifecycle does not claim to have fixed all Harness
storage growth. Native evaluation storage and its diagnostic-retention
contract are unchanged.

## Later-proven settlement and expiry

Retention at completion is not permanent. A retained allocation becomes
reclaimable later, under the namespace lock and with a fresh record generation,
when one bounded process observation (an `lsof +D` equivalent: libproc on
macOS, `/proc` on Linux) shows that:

- no visible process has its cwd, root, executable, mapped or open file inside
  the allocation, and
- the recorded owner leader is gone and nothing remains in its process group or
  session.

Two rules use that observation. `settlement_proven_later` applies to an
allocation its owner finalized with a passed outcome and that was retained only
as `descendant_settlement_unproven`, once its record is older than
`HARNESS_STORAGE_SETTLEMENT_GRACE_SECONDS` (default 15 minutes).
`retention_expired` applies to anything else retained or abandoned (failed
outcomes, unfinalized allocations) after `HARNESS_STORAGE_RETENTION_SECONDS`
(default 3 days, `0` disables it). Retained verification-fixture and
operation scratch whose owner did not report a pass (failed, unknown or never
finalized) uses `HARNESS_STORAGE_FAILED_RETENTION_SECONDS` instead (default 1
day): it is most of what accumulates. Under disk pressure, when free space is
below `HARNESS_STORAGE_PRESSURE_FREE_BYTES` (default 15 GiB) or the soft
warning is raised, retained scratch older than
`HARNESS_STORAGE_PRESSURE_RETENTION_SECONDS` (default 1 hour) is reclaimed;
allocations still `ALLOCATED` or `RUNNING` keep the normal windows.
`HARNESS_STORAGE_RETENTION_SECONDS=0` disables every expiry rule. Allocations with consumers, an open cleanup
transaction, a live owner tree or a process inside them stay retained, and so
does every candidate when the observation is unsupported, unavailable or
incomplete (`settlement_undecidable`). The process observation cannot see
other users' processes, and on macOS the kernel withholds the paths of
data-vault-protected descriptors; neither can live in private user scratch.
A late reclaim records a `REMOVED` receipt with the bytes it unlinked; a later
finalization of the same allocation returns that receipt and never changes the
evidence result.

A bounded pass (2 seconds, 10 under disk pressure) runs before each new allocation, so every Harness
check, AL evidence run and `verify_changed` run reclaims settled scratch first.
The explicit command is a dry run unless `--apply` is given:

```sh
harness storage prune            # or: alctl storage prune
harness storage prune --apply --seconds 300
```

`alctl doctor` reports retained allocation counts by reason and the retained
bytes it could measure within its budget (with a labelled extrapolation when
only part fits).

## Source materialization

The source fixture, CLI snapshot, and runtime-pin paths now use a shared
materializer for selected regular source files. Before a clone attempt, it
creates a complete ordinary-copy reference in the pinned destination directory.
It retains a system copy-on-write clone only after content checks and complete
descriptor-bound metadata observations match that reference. Those observations
include every extended attribute and distinguish absent ACLs from present
empty ACLs. Unknown or malformed observations remain errors; stable profile
mismatches use the ordinary-copy path. The materializer does not remove or
rewrite source security metadata. Cloned files have separate inodes; the
filesystem may share their data extents until one copy changes.

Unsupported clone symbols, filesystems, cross-device operations, or metadata
observation capabilities use the existing exclusive ordinary-copy behavior.
Permission errors, destination collisions, malformed results, integrity
failures, I/O errors, and storage exhaustion remain errors and do not silently
trigger a copy fallback. The reference is removed before success; cleanup
failures remain visible. Source and destination symlinks are refused. Captured
publication still checks the captured bytes and mode before writing, and the
surrounding bootstrap and pin flows keep their existing validation and
publication boundaries. The complete temporary reference adds I/O and peak
space during materialization; current functional checks do not measure that
cost or establish physical-space savings.

This optimization creates no hardlinks, shared cache, cleanup authority, or
retention exception. It does not establish descendant settlement, reclaim
existing data, or impose a storage cap. Focused clone and independence tests
show behavior, not bytes saved; no physical-space savings measurement is
claimed here. Filesystem free-space observations and summed allocated blocks
remain separate and cannot attribute shared APFS extents to individual files.

## Inspect, preview, and apply

Use the public CLI without opening a project database:

```sh
harness storage inspect
harness storage inspect --include-legacy --json
harness storage preview --output /tmp/harness-storage-plan.json
harness storage apply --plan /tmp/harness-storage-plan.json
```

Preview is bounded and binds the observed allocation identities and
publication/completion references. Apply rechecks those bindings. A stale plan
must be refreshed; partial cleanup can be resumed through a new preview and
apply. `inspect` may report partial coverage when its entry, depth, record, or
time budget is reached. `UNREGISTERED` legacy observations are informational
and are never cleanup authority.

The report separates logical file bytes from allocated-byte estimates. Hard
links are counted once within the observed set. APFS clones, open files and
concurrent activity mean that an estimate is not a promise about released disk
space; any observed filesystem free-space change is a separate measurement.

## Pressure and limits

The defaults are a 10 GiB managed-scratch warning and a 5 GiB minimum
filesystem-free-space reserve. They are soft admission/reporting thresholds,
not a hard quota on all Harness data or on a child process. Eligible allocations
may be reclaimed within bounded work; failed, unknown, active, pinned, durable,
and unregistered data remains retained. Pressure reporting should identify
retention reasons so an operator can choose a safe next step.

No existing user data is reclaimed by installing this lifecycle. In particular,
this change makes no claim that a large historical Rust target or old
verification directories are disposable. Use current ownership and explicit
review before any separate legacy cleanup.
