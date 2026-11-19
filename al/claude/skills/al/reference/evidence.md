# Evidence supersession and isolated setup

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

`evidence supersede --id EOLD --replaced-by ENEW --reason TEXT` appends an immutable audit artifact. Both entries and their logs remain unchanged, including the original FAIL. The replacement must be a newer original execution, a current VALID PASS of the same declared obligation and current plan. A later failed/running attempt wins. Changed command semantics require separate supported replanning; free text cannot assert equivalence. Corrupt logs, unresolved processes, foreign runs and conflicting replay refuse without changing state. Exact replay is idempotent while its binding is current.

This records why a current rerun replaces obsolete evidence; it does not resurrect old receipts, clear review findings or retire gates. Evidence status and inspection expose the relation. Area/repro readiness still uses the current command and latest-attempt ledger rules. `evidence retire` remains a distinct mistaken-plan operation.

Package-backed hooks use source-selected locked setup in disposable checkout storage. Setup records command, input hashes, executable identity, timing, outcome and cleanup before the hook. Missing tools, failed install and unsafe changed inputs are setup INFRA; code has not passed. Hooks then execute against prepared dependencies. Local environment paths and caches stay inside owned temporary storage; no global installs, generated locks or working-tree dependency links are used. General checkout symlink guards remain strict; only the owned venv link to the independently bound running interpreter is materialized as a copy.

Diagnostic status commands can exit zero while displaying FAIL or BLOCKED. Execution/refusal commands return nonzero when they do not succeed. Actual provider pending/manual/canceled results are recorded as unsuccessful verification, never code PASS or an invented provider test failure.

## Derive evidence recipes from CI sources

Before registering a lint or type check, inspect the actual CI job and its local
wrappers, then record the source paths and current hashes that define the recipe.
Preserve the exact tool target and flags, configuration, working directory, setup
prerequisites, environment values, and whether each wrapper step is required or
advisory. A CI wrapper such as `fc_step` executes the supplied command; it does
not imply additional targets. Do not infer a broader command from a job name.

Keep the command target separate from `--scope`: target arguments are passed to
the tool, while scope lists every source, fixture, script, configuration,
dependency manifest, and imported dependency that can affect reuse. Adding a
file to scope must not add it to the tool command. A changed-file check belongs
in its own named area. A component PASS does not establish that the complete CI
job passed.

`--env NAME` records and fingerprints the observed environment dependency; it
does not assign `NAME` in the check process. For an approved, tracked,
non-secret literal, put the assignment in the registered argv with the existing
`env NAME=value PROGRAM ...` form. Never copy ambient secrets into argv,
artifacts, or logs. Relative paths in argv resolve from the registered check
cwd.

The evidence runner does not create an isolated checkout or run package setup.
Prepare dependencies only in an already authorized, owned environment and
preserve the CI setup command and lock inputs. Generic `uv sync --locked` is not
proof of equivalence to a CI command with different extras or frozen semantics.
If the managed profile cannot represent the source recipe or setup, report that
verification gap; do not bypass the profile or execute a guessed substitute.

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
