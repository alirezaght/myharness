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
