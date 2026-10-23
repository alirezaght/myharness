# Phase 3 status: 2026-10-23

This supersedes the "work still required" list in `2026-10-21-al-harness-checkpoint.md`. All work is committed locally on `codex/al-harness-checkpoint-2026-10-21` from `b6156a9` through the commits listed below; nothing was pushed. The uncommitted Phase 2 files and the untracked selector fixtures were left untouched.

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

## Native acceptance (one bounded run per host, 180 s cap)

`scripts/smoke_phase3_native.py` now bootstraps the candidate launcher in a disposable root, because the shipped skill names `$HOME/.local/bin/alctl`, and the global launcher there is an older snapshot.

- Claude 2.1.309: the Harness worker started the native host; AL run `67f5d419` was admitted through the candidate launcher and the native session id was recorded; the run hit the 180 s cap (interrupted) before an AL result.
- Codex 0.163.0: AL run `681f3a11` was admitted through the candidate launcher and the native id was recorded; Codex ended its turn (exit 0) without a guarded AL result within the cap.

Launch, run admission and native identity work on both hosts with the candidate. A complete native AL lifecycle was not demonstrated inside the bounded window.

## Waiting for the user

1. **Global install.** `scripts/install_global.py plan|apply|verify` requires a terminal-approved AL run whose candidate is this source tree. Producing that approval means one AL run on this repository through guarded approval (the public journey test exercises the same path on a disposable copy). Nothing global was installed.
2. **Local AL cleanup.** A read-only inventory found 35 installed AL skill/agent copies outside this repository (amber worktrees, `amber-*` checkouts, `menu-platform`, `new-company-service`, `match-orchestrator`, `ai-profile`, ...). Run histories (`.claude/al-runtime`, `.codex/al-runtime`, 20 directories) should be preserved. The `al-shared-import-gate` and `financing-launch-features` amber worktrees may hold active pinned runs and need care. Nothing was removed.

## Residual items

- The generic `loc` gate category still discovers the conventional `scripts/check-loc.py` checker; it could move into the repository contract.
- Claude-adapter changes now run against the remaining Claude suites only (accepted with the pruning).
- Real-home planning enumerates about 506k directories under `~/personal` and `~/Documents`; the installer's 200,000-directory limit has not been redesigned.
