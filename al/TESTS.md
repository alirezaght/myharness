# AL test suites: cost, structure and invariant map

This file records why the AL suites cost what they cost and how they are kept fast (#36).

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
