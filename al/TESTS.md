# AL test suites: cost, structure and invariant map

This file records why the AL suites cost what they cost and how they are kept fast (#36):
the slowest files and their causes, the prebuilt run states, the single-run scheme for
host-neutral tests, and the invariant map of the regression batches with the reason for
every deleted test.

## 1. The 30 slowest files (baseline, before #36)

"Recorded" is the per-test sum from `al/verification-tiers.json` as recorded before epic #29
(process-seconds under the verification runner). "Now" is a single-process wall time of the
whole file after epic #29 on a loaded 8-worker machine (`>300` = stopped at the 5-minute cap).

| # | File | Tests | Recorded s | s/test | Now s | Why it is slow |
|---|------|------:|-----------:|-------:|------:|----------------|
| 1 | `installer:al/tests/test_reviewed_runtime_update.py` | 93 | 5942 | 63.9 | >300 | `enrolled` per test: fetches the real v0.1.7/v0.1.8 tags into a bare repository, materializes the historical source, bootstraps two launchers and runs the update transaction through real `alctl` subprocesses. |
| 2 | `installer:al/tests/test_operator_update.py` | 91 | 4719 | 51.9 | >300 | Each test copies the AL source, bootstraps a protected launcher and drives install/update/rollback through real `alctl` subprocesses. |
| 3 | `installer:al/tests/test_proof_initialization.py` | 34 | 2234 | 65.7 | >300 | `launched` per test (source copy + launcher bootstrap, 7-12 s), then every AL command is a launcher subprocess that verifies the pin again; parametrized over both platforms. |
| 4 | `installer:al/tests/test_planning_admission.py` | 17 | 1247 | 73.3 | >300 | Same `launched` cost plus a full planning packet/report/admission journey through launcher subprocesses per test. |
| 5 | `installer:al/tests/test_documentation_update.py` | 39 | 953 | 24.4 | >300 | Resolves and builds manifests of real tagged releases and runs documentation-only update transactions per test. |
| 6 | `codex:tests/test_runtime_batch6.py` | 64 | 771 | 12.0 | >300 | Each test builds a complete admitted SMALL run (start, spec gate, classify, gates, planning admission, writer, verification evidence) before one assertion. |
| 7 | `codex:tests/test_rework_recovery.py` | 46 | 768 | 16.7 | >300 | Each test builds an admitted STANDARD run into FINALIZATION with a red repro (about 10 s), then exhausts rework credits through more runtime commands. |
| 8 | `claude:tests/test_rework_recovery.py` | 46 | 749 | 16.3 | >300 | Byte-identical copy of #7 run a second time on the Claude host. |
| 9 | `codex:tests/test_runtime_batch12_replay.py` | 22 | 729 | 33.1 | >300 | Replays the guard policies against real baseline and candidate checkouts, executing every declared check, per test. |
| 10 | `claude:tests/test_runtime_batch6.py` | 64 | 715 | 11.2 | >300 | Host copy of #6 (the file differs slightly between hosts). |
| 11 | `installer:al/tests/test_runtime_run.py` | 15 | 666 | 44.4 | >300 | `launched` per test plus public-run dispatch through the real launcher child. |
| 12 | `installer:al/tests/test_runtime_pin.py` | 18 | 653 | 36.3 | >300 | Prepares several real runtime pins per test (full materialization with metadata capture, about 5 s each). |
| 13 | `installer:al/tests/test_activity_workflow.py` | 29 | 626 | 21.6 | 182 | Bootstraps a launcher and drives activity/retirement flows through launcher subprocesses. |
| 14 | `installer:al/tests/test_review_behavior_proof.py` | 4 | 553 | 138.3 | >300 | `launched` plus an entire run up to fresh review through launcher subprocesses, per test. |
| 15 | `installer:al/tests/test_terminal_queries.py` | 2 | 548 | 273.8 | - | Real terminal queries through the installed validator across moving snapshot/origin boundaries; many pinned child processes. |
| 16 | `installer:al/tests/test_rework_recovery_pin.py` | 8 | 536 | 67.0 | - | `launched` plus a full rework-exhaustion run through launcher subprocesses. |
| 17 | `installer:al/tests/test_validator_origin.py` | 36 | 530 | 14.7 | - | Copies the AL source and bootstraps or snapshots validators per test; spawns validator children. |
| 18 | `installer:al/tests/test_pinned_terminal.py` | 2 | 503 | 251.5 | - | One complete approved run through the installed validator seam (every command a launcher subprocess). |
| 19 | `installer:al/tests/test_release.py` | 34 | 503 | 14.8 | >300 | Builds and verifies release layouts from real tags (Git archive, materialization) per test. |
| 20 | `codex:tests/test_workspace_source.py` | 3 | 500 | 166.8 | - | Installs a real launcher and runs a public approval through the isolated installer query. |
| 21 | `claude:tests/test_workspace_source.py` | 3 | 466 | 155.3 | - | Host copy of #20. |
| 22 | `codex:tests/test_runtime_batch4.py` | 15 | 456 | 30.4 | - | Admitted SMALL run per test, then rebase/review flows. |
| 23 | `codex:tests/test_runtime_batch7.py` | 16 | 382 | 23.8 | - | Admitted SMALL run per test, then environment and stage-attempt flows. |
| 24 | `installer:al/tests/test_transactions.py` | 16 | 371 | 23.2 | - | Real install transactions with injected failures and rollback per test. |
| 25 | `codex:tests/test_runtime_batch5.py` | 46 | 352 | 7.6 | >300 | Admitted runs plus real discovery/retry subprocesses per test. |
| 26 | `installer:al/tests/test_runtime_successor_guidance.py` | 16 | 341 | 21.3 | - | `launched` and successor runtime pins per test. |
| 27 | `codex:tests/test_rework.py` | 19 | 309 | 16.3 | - | Admitted STANDARD run into FINALIZATION with a red repro per test (about 10 s) before one routing assertion. |
| 28 | `claude:tests/test_rework.py` | 19 | 298 | 15.7 | - | Host copy of #27. |
| 29 | `installer:al/tests/test_recovery_guidance.py` | 2 | 296 | 147.9 | - | Two long recovery journeys through launcher subprocesses. |
| 30 | `codex:tests/test_state_machine.py` | 8 | 283 | 35.4 | - | Whole state-machine journeys (many commands per test). |

The causes fall into four groups:

1. **An admitted run is rebuilt for every test** (host suites, #6-8, #10, #22-23, #25, #27-28, #30):
   start, spec gate, classify, gate plan and base run, planning admission and phase changes cost
   9-12 s before the first assertion. A profile of `test_rework.py` shows 11 s of setup and 0.6 s
   of the command under test.
2. **A protected launcher is bootstrapped for every test and every command is a launcher
   subprocess** (installer suite, #3-4, #11, #13-18, #26, #29): `launched` copies the AL source and
   bootstraps (7-12 s); each command then verifies the pin in a new process.
3. **Real releases are materialized per test** (#1-2, #5, #19, #24): historical tags are fetched,
   materialized and installed or updated through real transactions.
4. **Everything shared runs twice**: 134 test files are byte-identical in both host suites.

## 2. Prebuilt run states

See `al/<host>/skills/al/tests/run_templates.py`. A test that only needs to start from an
admitted run asks for it as its first action:

```python
def test_x(cli, run_state):
    run_state("implemented")   # instead of start_implementation(cli, tmp_path)
```

| State | Equivalent helper | Content |
|-------|-------------------|---------|
| `planned` | - | STANDARD run, N/A gates planned and base-run, planning admitted |
| `implemented` | `evidence_helpers.start_implementation(cli, tmp_path)` | `planned` + phase IMPLEMENTATION |
| `finalizing` | `test_rework.started(cli, tmp_path)` | `implemented` + phase FINALIZATION + one failing repro run |
| `verified` | `hook_regression_helpers.begin(cli, tmp_path, repo)` | SMALL run with a finished writer, candidate gates, phase VERIFICATION and passing broad evidence |

The first request in a pytest process builds the state with the real public commands and
records a template; every later request restores a private copy. Isolation is proved by
`tests/test_run_templates.py`:

* **No shared mutable state.** The template lives outside every test's paths and is never handed
  out. Each opted-in test's repository is recreated at one per-process path; the run's launcher
  transaction, the content-addressed initialization record it cites and the analysis index are
  replaced from the template; the AL scripts' in-process memo tables (found by scanning every
  script module before any test runs) are emptied.
* **A mutation never leaks.** The proof mutates source, Git data, runtime state, transaction,
  initialization record and index, checks the template is unchanged, restores, and checks the copy
  is the template again.
* **Byte-identical start.** Every restore digests every part (paths, bytes, permission bits,
  symlink targets) and refuses to continue unless the digest equals the template's. A test that
  touched its repository before calling `run_state` is refused.

The installer suite's `launched` fixture (`al/tests/runtime_run_helpers.py`) uses the same scheme:
the source copy and launcher bootstrap (7-12 s) run once per process at one fixed path, and each
test gets a copy verified byte-identical (`al/tests/tree_templates.py`) to what the bootstrap left.

Converted: every test whose first action was `start_implementation(cli, tmp_path)`,
`test_rework.started(cli, tmp_path)` or `hook_regression_helpers.begin(cli, tmp_path, repo)` with
default arguments (`test_rework`, `test_rework_recovery`, `test_rework_implementation_continuity`,
`test_evidence_integrity`, `test_evidence_execution`, `test_delta_review`, `test_brief`,
`test_acceptance_states`, `test_output_contracts`, `test_review_contract`, `test_model_slots`,
`test_hook_batch7`, `test_state_machine`, `test_runtime_batch4/5/6/7`,
`test_runtime_batch7_inventory`, `test_runtime_batch15_recovery`), plus every installer test that
uses `launched`. Same-load A/B: the first 8 tests of `test_rework_recovery` took 132 s before and
61 s after.

## 3. Decision functions and end-to-end journeys

A test whose assertion concerns one decision function calls that function directly; one public
journey per outcome remains. Example: the six rework triggers were six full runs
(`test_rework.py::test_a_real_decision_still_reaches_the_architect[...]`); now one public run proves
the exit code and route, and `test_every_decision_trigger_routes_to_the_architect_and_none_takes_the_fast_path`
proves every trigger, its exact reason and the fast path through `al_rework.validated_request` and
`consume_return`.

The end-to-end journeys that stay public: the SMALL, STANDARD and COMPLEX routes of
`test_state_machine.py` and `test_runtime_batch6.py::test_changed_rules_replan_then_complete_fresh_approval`,
the acceptance tier (`acceptance` in `al/verification-obligations.json`), and the key refusals in
`test_core_hardening.py` and `test_hook_regressions.py`.

## 4. Regression batches: invariant map and deletions

Every test of `test_runtime_batch*`, `test_hook_batch*` and `test_hook_regressions` was mapped to
the invariant it guards and searched for in the whole suite. A test was deleted only when another
test on the same host runs the same code path with the same or stronger setup and asserts a
superset. Everything else is kept, including tests that look alike but differ in one assertion.

### Deleted

| Deleted | Host | Covered by | Reason |
|---|---|---|---|
| `test_hook_regressions.py::test_h166_4_unresolved_prior_blocker_remains_blocking` | both | `test_runtime_batch6.py::test_comparison_disposition_never_clears_inherited_blocker[unresolved]` | Same `al_traceability.validate` path through public `review-result`; asserts the exact blocker count and a refused, byte-unchanged FINAL_VERIFICATION, where h166_4 accepted "> 0 or SystemExit". |
| `test_hook_batch6.py::test_second_plan_conflict_needs_its_own_architect_resolution` | both | `test_runtime_batch6.py::test_conflict_transition_and_workflow_share_current_proof[new]` | Same COMPLEX PSEUDOCODE -> PLAN_CONFLICT sequence; asserts the refusal, unchanged state, the new resolution and its history. |
| `test_hook_batch8.py::test_uv_separator_cannot_hide_git_reset_from_shared_ownership` | both | `test_runtime_batch8_shell.py::test_owner_negative_receipt_cannot_advance_or_authorize` | Same `ownership` command and refusal, plus phase not advanced and the recorded diagnostic. |
| `test_runtime_batch14_cancel.py::test_pending_signal_during_public_child_commit_cannot_persist_late_pass` | both | `test_runtime_batch14_commit_race.py::test_locked_observer_cannot_see_transient_pass_before_cancelled_finalization` | Same SIGTERM-at-PASS-save seam in a real child; its one unique assertion (`'SIGTERM' in note`) moved into the covering test. |
| `test_hook_regressions.py::test_h166_8_environment_command_strings_are_rejected_by_both_shell_policies` | codex | `test_runtime_batch4.py::test_wrappers_normalize_paths_and_refuse_unknown_options` | Same two commands under both policies, among five more. The Claude copy stays (Claude has no batch4). |
| `test_hook_regressions.py::test_h166_3_listing_wizard_alpine_script_cannot_claim_ui_is_not_applicable` | codex | `test_runtime_batch4.py::test_browser_scripts_require_rendered_evidence[...listingWizard.ts]` | Same seed, path, candidate run and refused `ui.applicable=False` comparison. |
| `test_hook_regressions.py::test_h166_6_fresh_correction_report_does_not_need_historical_resolution` | codex | `test_runtime_batch4.py::test_correction_fresh_is_independent_and_comparison_requires_fixed_prior_resolution` | Same correction route and accepted new fresh review, plus the comparison rules. |
| `test_hook_batch5.py::test_discovery_passes_supported_directories_to_loc_checker` | codex | `test_runtime_batch5.py::test_loc_directory_dedup_deleted_path_and_expanded_scope` | Same fixture checker and discovery; asserts the exact argv and scope and the real rc 0. |
| `test_rework.py::test_a_real_decision_still_reaches_the_architect` (5 of 6 cases) | both | `test_rework.py::test_every_decision_trigger_routes_to_the_architect_and_none_takes_the_fast_path` and the kept public case | Per-trigger decision pushed down (section 3). |

### Kept, with the invariant each guards

| Test (hook batches, regressions) | Invariant |
|---|---|
| regressions::h166_1 | `resume` after a candidate change in PLANNING stays in PLANNING and completes nothing later |
| regressions::h166_2 | distribution apply never claims or deletes a pre-existing identical unmanaged file |
| regressions::h166_3, h166_6, h166_8 (claude) | as above; Claude has no covering batch4 |
| regressions::h166_8 native hook (claude) | the read-only hook denies `env -S` and `/usr/bin/env cp` |
| regressions::h166_5 | `start --target` binds baseline_head to the task HEAD; the candidate gate sees a deletion |
| regressions::h166_7 (two tests) | review-start and approval refuse an unrun base gate of a newly planned manifest |
| regressions::h166_9 | `review-compare-start` without `--plan` is refused without a state write |
| hook_batch5::full service name | discovery keeps a multi-hyphen service name; the architecture check really fails |
| hook_batch5::contract discovery | the contract chain is in scope; edits change the execution signature |
| hook_batch5::single guarded retry | one retry clears the failure; the second exits 7 and stops |
| hook_batch5::fresh blocker survives | a fresh blocker left `unresolved` must be resolved in round 2 |
| hook_batch5::git output (+ claude hook) | `git --output` really writes; both policies and the native hook deny it |
| hook_batch5::LOC checker (claude) | Claude has no covering batch5 |
| hook_batch6::changed sourced rule | an edited planned rule source forces a replan on resume |
| hook_batch6::amendment | after `task amend`, review-start needs the candidate gate rerun |
| hook_batch6::unsatisfied fresh criterion | an UNSATISFIED criterion without a same-report blocker is refused ("AC-01") |
| hook_batch6::external diff | `-c diff.external` really executes; policies, ownership (codex) and native hook (claude) deny it |
| hook_batch6::fresh resolution | a fresh report cannot clear a blocker with an embedded `fixed` resolution |
| hook_batch7 (5) | env change stales the candidate gate and reaches the child; primary review survives fallback; discovery cannot be erased; shared package selects the importer; write scripts are not owner commands |
| hook_batch8 (3) | wrapped search is admitted; CI manifests must cover each touched unit; full coverage is accepted |

Runtime batches 4-17: every test is kept. Each has at least one assertion or setup no other test
has. Per file:

| File | Invariants guarded |
|---|---|
| runtime_batch4 (codex) | changed-candidate resume keeps unfinished phases; post-implementation resume returns to VERIFICATION; browser scripts need rendered evidence, server TS does not; correction fresh is independent and comparison needs a fixed resolution; the plan hash must be inspected; an extended manifest needs base gates; fix decisions bind to manifest and failure; wrapper paths normalize; Designer comparison needs pseudocode; approval rechecks real completion |
| runtime_batch5 (codex) | the service check uses the full path and really fails; unknown recipes are unresolved; LOC gate shape and escapes; retry preconditions (failure, checkpoint identity, writer and check reconciliation, peer labels); second-round fixed resolutions need valid proof; git option values are data and `--output` fails closed |
| runtime_batch6 | changed rules replan then approve; new units replan; removed rules refresh; corrupt recovery artifacts refuse; candidate gates need full ledger identity; unsatisfied criteria need a linked unfixed finding; conflict proof is per occurrence and role; git execution overrides are denied (claude also via the native hook); fresh reports carry no resolutions; dispositions never clear inherited blockers; quoted commands and comma lists are checked |
| runtime_batch6_recovery (codex) | recovered phases replace gates before advancing; a failed base admits writers but holds review; mismatched markers and running checks block replacement |
| runtime_batch7 (codex) | env declarations are validated; env changes stale every stage and cannot authorize a failed baseline; fallback binds the current policy; attempt replacement and provenance |
| runtime_batch7_inventory | prior paths and rules cannot be omitted; new instruction sources stale currency; contract obligations cannot be N/A; transitive consumers and env dependencies are kept |
| runtime_batch7/8/9/11/12/14/15/16/17 shell and policy files, claude `*_native` files | deny/allow tables for `al_shell.check` (both policies) and the native hook; no command repeats across files; public ownership refusals record a diagnostic without advancing |
| runtime_batch8_ci, 8_context, 9_ci, 10_ci | GitLab CI inventory: one job per unit; include, anchor and extends sourcing; unknown rules stay UNRESOLVED and cannot be forced; context binding and tampering; stage and environment classification; false-rule exclusions |
| runtime_batch9_rollout | Alembic changes trigger rollout review; N/A or missing dimensions are refused |
| runtime_batch11_distribution | subset adoption ownership, drift, rationale, interrupted transactions, forged prior ownership |
| runtime_batch11_recovery | repeated amendments rebind recovery checkpoints; corrupt pre-amendment recovery refuses |
| runtime_batch12_replay, 15_replay | guard replay accounts every policy, runs baseline and candidate independently, never matches errors or timeouts, never executes command text |
| runtime_batch13_checkout, 13_remote (codex) | unsafe links and cwd fail before launch; malformed remote responses are INFRA |
| runtime_batch14_cancel, commit_race | signals reap the process group and finalize; no late PASS at any seam |
| runtime_batch14_consumers, 14_context, 16_writers | body-only edits, changed obligations, active operations, corrupt markers, CI context history, writer admission coverage |
| runtime_batch15_recovery | stopping commands on finished runs; stop/resume and retry occurrences; terminalization |

## 5. Host-neutral tests run once

`al/codex/skills/al/tests/single-run.json` lists 102 test files that exercise only shared
scripts through the host-adapter fixtures (`cli`, `cli_process`, `state_file`, `launcher`,
`run_state`; each host's `conftest.py` defines them for its own runtime layout). They run once,
in the Codex suite; their Claude copies were removed. The shared scripts they exercise are
byte-identical in both hosts (`test_parity.py`), so a second run proves nothing new.

A file keeps a copy in both suites when it differs between hosts, when its name marks a host
boundary (native, session, hook, statusline, read-only, model, parity, consistency, docs,
resources, launcher, start, state machine, run templates), when it or a helper it imports uses a
host-specific script (`al_state`, `al_start`, `al_models` and the Claude-only adapters) or names a
host (`.codex`, `.claude`, a platform comparison), or when a remaining Claude module imports it.
`test_single_run.py` enforces the rule for every listed file, so a host-specific dependency cannot
hide in a single-run file. `verification_affected.py` treats a change to either byte-identical
copy of a shared script or resource as a change to both, so `verify_changed.py` still selects the
single-run tests when only the Claude copy is named.

## 6. No test touches the real AL registry (#37)

Tests keep AL state under their own temporary roots. The autouse fixtures of
`al/tests/conftest.py` and `tests/conftest.py` point `AL_DISTRIBUTION_STATE` at the
test's `tmp_path`; the Harness suite also sets `HARNESS_ALCTL` to a missing path and
removes a global `alctl` from `PATH`, so a test reaches AL only through a launcher it
bootstraps itself. A session guard (`al/tests/registry_guard.py`) snapshots the real
`~/.local/state/al-distribution/registry` of the account and fails the session when a
run binding or initialization record that names a temporary directory appears there;
other changes (a concurrent real AL run) only warn. Archive leaked bindings whose
repository is gone with `alctl prune-stale-runs --apply`.
