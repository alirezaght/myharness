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
