# Phase 3 status: 2026-10-23

This supersedes the "work still required" list in `2026-10-21-al-harness-checkpoint.md`. All work is on `main` (the `codex/al-harness-checkpoint-2026-10-21` history from `b6156a9`, merged by fast-forward). The uncommitted Phase 2 files and the untracked selector fixtures were left untouched.

## Done

| Area | Result | Commits |
| --- | --- | --- |
| E525 planner deadlock and validator cost | Coordination lock token; proportional, exact-content analysis cache | `b6156a9` |
| Mandatory managed launcher | Every AL run command goes through `alctl run`; suites, Harness execution and replay migrated; Claude native session minting restored | `7e51c5b`, `ecc745c` |
| Amber removal | Repository-specific discovery only through a repository's own closed, fail-closed `.al/repository.json` contract; persisted `amber` state refused; worktree contract edits are blocking `contract-changed` gaps | `80bf6ed`, `e4f9d25` |
| Artifact consumer discovery (pending requirement 1) | OpenAPI schemas, data fixtures and generated code route to their literal consumers and importers; unrelated import uncertainty is deferred only when it provably cannot reach affected modules | `e1549de`, `e4f9d25` |
| Review-hook state (pending requirement 2) | Declared isolated `.git/<name>` location: absent before execution, captured as a candidate-bound artifact, removed afterwards; other `.git` writes still fail; never reused through receipts | `e1549de`, `e4f9d25` |
| Baseline exception (pending requirement 3) | `gates decision --action exception`: authorized, append-only, bound to run/base/manifest/gate command/environment/failed evidence/log/execution identity; evidence stays FAIL; continuation runs remaining gates; candidate checks stay honest | `e1549de`, `e4f9d25` |
| Performance | Exact-input in-process reuse of Git metadata reads, decision windows, one duplicate workspace capture removed, duration-ordered shards, approved pruning of 13 byte-identical Claude duplicate suites | `3ca6959`, `5faa718`, `f14824e`, `c782c33`, `30cab45`, `7d1490e`, `19b386e` |
| Complete verification | `release_verify --full` passes every collected test with complete accounting (see measurements) | `8a39ae7`, `228d991`, `2e97aaa` |
| Independent review | Three read-only reviews (performance memo, requirement features, amber removal/pruning); all blocking, major and minor findings fixed with regressions | `e4f9d25` |
| Browser acceptance | `tests/test_dashboard_execution_browser.py` in real Chrome at 1280x800 and 390x844; six screenshots reviewed (hostile text inert, draft preserved, no page overflow; mobile task cards scroll inside their container) | n/a |

The supplied baseline-continuation/hook-audit references were verified (`/private/tmp/al-base-recovery-*-ref.json` exist and match their artifacts). The repaired package in `/private/tmp/al-hook-repair-active` belongs to an older, divergent distribution and was not modified; its hook-audit semantics informed the generic review-state design. No amber worktree was modified.

## Measurements

- Complete suites, user accepted on 2026-10-23: `release_verify --full --workers 8` in a clean worktree of `2e97aaa` passed 4,329/4,329 with complete accounting in 853 s (816 s for 4,292 tests before the review fixes). The run was perfectly packed (6,460 shard-seconds / 8 workers) and is bound by total work; the public workspace journey alone takes ≈360 s under load. **The 360 s target is not met**; end-to-end proofs stay in the complete suite by decision. Before this work: 26,400 test-seconds and 2,725 s wall (scratch runner, 12 workers).
- Public workspace journey (plan/apply/verify for both platforms): 358 s → 241 s, passing.
- `test_readiness_reservation` fails only when pytest's parent process is a shell; it passes under `release_verify` and a Python parent.

## Native acceptance (2026-10-24)

`scripts/smoke_phase3_native.py` runs one real host session in a disposable project (now with an installable package and one CI job AL can run locally) through the candidate launcher, bounded at up to an hour.

- Claude: AL run `761897ab` went from harness approval through planning, implementation, gates, fresh and comparison reviews to an approved result in 23 minutes; the harness recorded the execution `Completed`.
- Codex: AL run `fdfdc1a5` did the same in 13 minutes.

The attempts before these found and fixed: writer lifetimes opened after the writing agent started (skill rule), rework unable to reopen a completed execution task, and a scopeless preflight refused in a harness-bound run.

## AL policy change (2026-10-24)

AL no longer fails closed on checks it cannot model. An `UNRESOLVED` gate (unparsed CI rules, unsupported workspace layouts, incomplete service coverage) is an unverified check: `gates status` lists it (CI jobs as verified by the pipeline), reviewers see it in the manifest, and the final report names it, but it does not block admission, review or approval. Failed, running or interrupted checks and writer/attribution safety still block. Discovery also expands pnpm-style workspace globs, declares `parent-entry` for gitlinks a gate covers, binds import targets by digest, and allows four times larger fact sets.

## Installation and cleanup (done 2026-10-23)

The guarded `install_global.py` flow was abandoned at the user's request after repeated refusals on leftovers. AL was installed globally by copying both packages and role files and bootstrapping `alctl`; every repository-local AL copy under `~/personal` and `~/Documents` outside myharness was deleted (deletions left uncommitted in those repositories; run histories kept). The old global copies are backed up in `~/.local/state/al-distribution/manual-backup-20261023/`. The installer registry does not know about this install, so `alctl doctor`/`update` report drift; updates are a manual copy and bootstrap.

## Residual items

- The installer registry could adopt the manual global install, or updates stay manual.
- The generic `loc` gate category still discovers the conventional `scripts/check-loc.py` checker; it could move into the repository contract.
- Claude-adapter changes now run against the remaining Claude suites only (accepted with the pruning).
- The 360-second complete-suite target remains unmet by decision (`release_verify --full` takes about 12 minutes).
