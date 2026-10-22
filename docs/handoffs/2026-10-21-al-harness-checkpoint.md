# AL and Harness continuation checkpoint — 2026-10-21

**Unfinished implementation checkpoint. Not a release, source approval, passing full-suite claim, or installation instruction.**

Implementation stopped under the user's weekly-quota rule. The last recorded allowance was 39% remaining; pause all implementation when remaining allowance is below 40%. All writers through `p3-engineer-efficiency-133` are FINISHED, all checks have ended, and owned cleanup for the last disposable journey completed. Builder remains HOLD. The user subsequently authorized committing/pushing this checkpoint and preparing this handoff; that does not authorize resuming implementation below the threshold.

## Objective and constraints

Complete Harness Phase 3 and make AL efficient and globally installable/updatable from this repository. Keep Claude and Codex distributions synchronized. Finish source verification and independent review before actual deployment. Then install AL and Harness globally and remove only attributed repo-local AL installations from repositories under `~/personal` and `~/Documents`, except this exact myharness repository.

The desired entry point installs/updates the coordinated system in one operation, with independently identified AL and Harness payloads. AL remains independently usable. Preserve unrelated files, configuration, instruction bytes, runtime histories, existing failures and evidence. Back up and journal every authorized installation/removal; verify globals before local removal. Do not copy other repositories' diagnostic state, source, paths or logs into this repository. Repository adapters may describe supported generic discovery contracts.

Implementation must use the installed AL orchestrator and the repository's AGENTS instructions. Superpowers is restricted to brainstorming and systematic debugging. Do not run its other development workflows. Atlassian is read-only unless a specific write is requested. Follow the shared database reference before any database work. Browser testing must assess visual alignment and UX as well as function.

## Current authoritative local run

- Session: `al-20261019T112752Z-d843eb`; run: `cbe11401`.
- Protected state: `.codex/al-runtime/al-20261019T112752Z-d843eb.json`.
- Recorded phase/status: IMPLEMENTATION / RUNNING; operational work is quota-paused. Do not fake a terminal or blocked status.
- Original baseline HEAD: `df8d0b571f1fa8829ade18faa3c7a95aa8703788`.
- Rework 2/2 spent; Engineer retry 1/1 and Architect retry 1/1 spent; review corrections 0/6. Never reset these by editing state or starting a replacement run to evade them.
- Current run uses its existing installed old issuer. Do not switch it to the newly built runtime, pin it retrospectively, rewrite its packets, or hot-update its issuer.
- Actual immutable issuer captures are external. Preserve `/private/tmp/harness-p3-actual-codex-issuer-v1/` and `...-v2/` unchanged.
- Original immutable task package is the artifact named `cb363d164db46496925aa83f9a509e5500c68551d11c3ef07dda3fd29f5bd6fb` under the run's `artifacts/` directory. Read it and every accepted amendment from state; this document does not supersede their full text.

The installed state command is:

```sh
HARNESS_DASHBOARD_SCREENSHOT_DIR=/private/tmp/harness-p3-dashboard-shots \
HARNESS_DASHBOARD_SCREENSHOT=/private/tmp/harness-p3-dashboard-shots/active.png \
python3 "$HOME/.codex/skills/al/scripts/al_state.py" \
  --session al-20261019T112752Z-d843eb show --panel
```

State commands lock/write the protected runtime directory, including some reads; obtain the environment's required sandbox approval. Never hand-edit state. Use the actual public help/operations. Every check must be recorded through `evidence run`; validate claims before phase advancement. STALE PASS is historical evidence only. Log digests use SHA-256 of executable-mode prefix (`NUL+x` or `NUL+-`) plus raw bytes; ordinary artifact digests use raw bytes.

If continuing in the same orchestration context, existing roles were `/root/p3_engineer`, `/root/p3_builder`, `/root/p3_plan`, `/root/p3_design`. Reuse them where available. Architect is Astra/MEDIUM; Designer Astra/LOW; Engineer Sol/MEDIUM; Builder Luna/HIGH. Model fallback is allowed only through AL's actual unavailable-model procedure. Another agent/session must use a supported ownership/resumption path, not assume these tool IDs exist in its context.

## Work implemented, with focused evidence

These are component milestones. Later edits invalidate earlier broad scopes. Revalidate against the final frozen source; do not sum these counts as a complete suite.

| Area | Implementation and limits |
| --- | --- |
| Scratch performance | Batched Git blob observation, shared-object checkout work, scoped observations and checkout/gate reuse. Preserve candidate race, mode, membership and failure semantics. Full final performance acceptance remains outstanding. |
| Service coverage | Three outcomes: identified dependencies; uncertain dependencies with proven exhaustive service/check coverage; incomplete coverage that blocks. Literal recognized external imports resolve; relevant uncertainty broadens coverage where complete. Opaque submodules, unsupported layouts and missing checks block. Generic policy is separate from repository adapters. |
| CI discovery | Bounded GitLab `environment.action: prepare/access` support and explicit unsupported contexts. Do not turn unsupported setup/cloud commands into reusable evidence. |
| Task attribution | New-run source snapshots and admitted-writer attribution distinguish task changes from external/ambiguous work. Public explicit reconciliation retains dependency checks. No retrospective attribution of this legacy run. |
| Gate continuation | Public resume/retry of missing/stale/interrupted/explicitly failed gates; preserve failures and completion history, global unresolved-operation barriers and nonzero whole-stage readiness. |
| Evidence reuse | Immutable original execution receipts, separate destination reservations/associations, input/source/log/environment/policy bindings and later-failure barriers. Positively supported reusable closures are narrow: isolated JSON reader and independently sourced bundled architecture reader. General arbitrary-command reuse is not complete. |
| Editorial changes | Closed display-only task/criterion/scenario overlays retain normative authority. No semantic equivalence guessed from prose or `wording_only` flag. V2 receipt/preparation/association bridges retain original documents and verify overlay lineage. Actual JSON-child tests show no duplicate execution across two overlays; tampered logs force execution. |
| New-run runtime pin | Managed `alctl run` dispatches under isolated `-I -S -B` startup using complete captured bytes/modes/resources and interpreter identity. Protected registry-derived storage, RESERVED/ACTIVE transaction and independent run/origin/initialization checks. Ambient HOME/PYTHONPATH and target modules cannot select the runtime. Legacy runs remain with their issuer. |
| Planning/proof admission | Every new candidate run starts pending. Real issued Architect attempt/packet, immutable report and noncircular obligation materialization precede separate authority admission. Pending blocks implementation/review/approval. Removing new fields cannot activate legacy behavior. |
| Sufficient behavior proof | Structured lifecycle, states/transitions, preservation/reset, real vs simulated boundaries, complete journeys, consumers and operational-failure obligations. Current evidence/assertion scopes and hashes must cover scenarios. Fresh review requires counterexample search; semantic quality remains the reviewer's job. |
| Independent review protocol | New packets allowlist task expectations and factual evidence, excluding implementation plan/pseudocode/prior findings from fresh review. Comparison receives plan later. Immutable packets can cross verified editorial overlays with explicit lineage. Actual disposable approval tests use fixture provider responses, not live model invocations. |
| Recovery guidance | One selector-bound typed next operation/input per blocker; revalidate current binding and original guards before action. Guidance grants no bypass authority. |
| Publication readiness | Reported pending/published-review-pending/failed facts stay nonterminal and separate code/review/release readiness. No MR transport or push implementation. `independently_verified` observer claims are deliberately unsupported/refused because no trusted observer exists. This boundary remains explicit. |
| Harness Phase 3 | Native CLI host bootstrap, approval-bound execution reservation, durable SQLite execution/event/log state, scoped blocker decisions, recovery/reconnect, dashboard/SSE execution projections and acceptance/demo fixtures. Claude default; Codex selectable. Final native/browser/full acceptance still owed. |
| Global packaging | Separate workspace payload provenance/cache from tagged releases; actual offline Harness wheel build, closed member inspection, exact wheel installation into owned environment, version/help/origin checks and interpreter identity. Shared owned process lifecycle preserves cleanup/identity checks. |
| Installed validation | Actual completed pinned runs can be queried read-only through installed immutable source-proof validator, with truthful code origins and separately verified execution-origin data. Hash-only caller-environment observation never restores untrusted execution variables. Full pin/policy equality required; unreviewed older policy is not waived. |

Main code map:

- `al/{codex,claude}/skills/al/scripts/`: mirrored runtime; `al_scope`, `al_checkouts`, `al_service_coverage`, repository adapters, execution receipts/projection/versions, `al_new_origin`, planning/authority/editorial, behavior observation/review, recovery and publication modules.
- `al/installer/runtime_pin*.py`, `runtime_run.py`, `validator_run_pin.py`, terminal/environment helpers: immutable startup and installed proof boundary.
- `al/installer/workspace*.py`: source/payload/tools/wheel/environment, scope/activity/ordinary admission, CLI/resources, request/plan/revalidation, actions/record/coordinated operation.
- `al/installer/owned_*.py`: shared isolated owned-process lifecycle; original verification-process import remains a compatibility alias.
- `al/installer/activity_*.py`: complete inventory, closed profiles, exact historical acknowledgements, process/lease/lock observations and historical retirement.
- `scripts/install_global.py`: isolated source entry point for registered `plan`, `apply`, `verify`; explicit file hash required. It is unfinished and must not be run against the actual home yet.
- `harness/execution*.py`, migration `0011_executions.sql`, CLI/dashboard modules, `static/execution.js`: Harness integration.
- `al/release-layout.json` and both parity manifests: complete runtime member declarations; tests/caches stay out of releases.
- `al/verification_*.py`, `al/verification-obligations.json`, `al/benchmark*.py`: full-suite orchestration/accounting; final timing not accepted.

## Latest exact results and current blocker

| Evidence | Result | Meaning |
| --- | --- | --- |
| E503 | PASS 2, 113.77s | Both actual disposable pinned-platform terminal runs queried by installed validator, with before/after mutation controls. |
| E508 | PASS 21, 2.39s | Actual offline Harness wheel/owned environment plus controls. |
| E509 / E510 | PASS 23 / PASS 10 | Workspace/tagged provenance and independently reconstructed repository/runtime-root scope. |
| E514 | PASS 94, 38.15s | Closed workspace activity selection plus historical/operation regression controls. |
| E516 | PASS 12, 8.86s | Exact old managed CLI receipt upgrade component; neutral installed help/list/doctor after source rename. |
| E517 / E518 | PASS 16 / PASS 27 | Plan/request primitives and isolated entrypoint/unchanged installer controls. Not the complete public journey. |
| E522 | FAIL 1, 326.97s | Public planner hit inherited 180s parent timeout; query child exited naturally. |
| E523 | FAIL 1, 483.87s | Genuine approval/activity and AL staging passed; actual wheel build succeeded, then exact uv output marker handling refused. |
| E524 | PASS 18, 1.23s | Fixed exact regular `.gitignore` marker handling, base-interpreter identity, and planned ancillary-preimage race controls. |
| E525 | FAIL 1, 751.82s | Latest public journey exceeded owned 600s diagnostic bound during planning. No public apply/verify success. Owned cleanup completed without unresolved operations. |
| E352 | FAIL: 3891 passed, 4 failed; 718.573s | Last complete AL suite. Fixtures later corrected in focused work, but no complete current rerun. Cold/warm <=360s target remains unmet/unproven. |

Latest command was `python3 -m pytest -q al/tests/test_workspace_journey.py`. The fixture first uses a genuine old public issuer and current-policy checks/reviews to obtain guarded terminal approval of a disposable full source tree. Then it invokes actual isolated workspace plan/apply/verify for both global platforms. It does not patch terminal state or fabricate historical acknowledgements. It has **never completed that full journey**.

Repeated fresh full-source validation occurs along `capture_source -> plan_activity -> activity_context -> ordinary assessment`, before/during staging. That repetition is a concrete investigation lead. Per-function durations were not measured; do not claim proven cost attribution, raise limits again merely to get green, or cache approval decisions/restore old PASS authority. Preserve fresh source, process, held capability and mutation-time guards while making observation work proportional.

No suite pruning/equivalence success is claimed. Full cold and warm complete-collection/accounting, including installer and both platform suites, must pass within the user's 5–6 minute requirement. Meaningful code, architecture, failure and review checks stay.

## Pending requirements added at checkpoint time

These user requirements were captured while implementation was paused. They are **not implemented or verified**. Incorporate them into the existing task through its supported amendment/admission path after quota/resume authorization; do not pretend they were already covered.

1. **Artifact-specific consumer discovery.** A changed shared JSON fixture must not automatically trigger Python dependency discovery across every service and inherit unrelated dynamic-import blockers. Distinguish fixtures, OpenAPI schemas, generated code and shared Python modules, derive their actual producers/consumers, and turn relevant unresolved dependencies into explicit verification requirements. Preserve proven exhaustive fallback and incomplete-coverage blockers. Existing coverage/reuse groundwork does not establish this routing behavior.
2. **Real review-hook state.** Support a declared isolated state location for a hook that writes under `.git/codex-review`. Bind its contents and provenance to the candidate, retain review/audit history, and account for it in scratch integrity and receipt validation. Do not generally allow arbitrary `.git` writes or let stale/foreign review state satisfy a candidate.

3. **Explicit baseline exception for implementation admission (added 2026-10-22).** Existing `fix` commits the task to repairing an unrelated baseline defect; `stop`/`defer` stop the entire run. Add a distinct explicitly authorized, auditable decision to record an existing unchanged-base failure, keep its repair outside this task, and admit feature implementation once all other requirements are satisfied. This is separate from failed-gate continuation and remains unimplemented here.

   - Bind authorization/provenance and the exception to the exact run, pinned base, manifest, gate, execution environment, command and failed evidence/log hash. Reject stale, mismatched, forged or tampered bindings.
   - Keep the original evidence **FAIL** and display the exception separately. Preserve every required service job, hook, architecture check and checkout-integrity safeguard.
   - Continue processing remaining baseline gates; a decision on one failure cannot satisfy other outstanding obligations. Candidate checks still run and report failures honestly. This decision grants no automatic final approval with red checks.
   - The supplied reproduction has an unchanged-base required UI test job with 65 passing and 3 failing tests. The cause is not established; do not assume production defects or harmless flakiness. Exact external project/session/base/gate/log details are retained outside Git in `/private/tmp/harness-p3-pending-baseline-exception-feedback.json`.
   - The user reports baseline continuation, hook-audit handling and shared-import selection already repaired in `/private/tmp/al-hook-repair-active/.codex/skills/al` and its Claude counterpart. Read and verify the supplied frozen/proof/distribution references before integration. Do not redo or overwrite those repairs or replace an active run's pinned runtime.
   - Future implementation is explicitly scoped to a **separate tooling worktree**. Do not modify the feature worktree or repair its unrelated UI functionality. Inspect `al_gates.py` baseline decision/readiness paths, writer admission in `al_gate_recovery.py`, transition admission in `al_transitions.py`, and the supplied repaired package's `tests/test_base_gate_recovery.py`. Add exact binding/tamper/staleness, remaining-gate continuation, honest candidate failure and final-approval refusal regressions.

   Reference-file hashes observed during documentation capture (existence/JSON checked, not a completed review of their referenced proof):

   | Reference | SHA-256 of reference-file bytes |
   | --- | --- |
   | `/private/tmp/al-base-recovery-frozen-ref.json` | `ca48aed864950cf1e3f900471fced234a6ece3146cd45029fcf2358858a24973` |
   | `/private/tmp/al-base-recovery-proof-ref.json` | `1db848b11a75ac55ca581f765fafde370492948c6deaeb191eb7303ee195b23c` |
   | `/private/tmp/al-base-recovery-distribution-inputs.json` | `f064ba8d70c8374b0dbb963ec74965d4cdffeac8e2f87d67c36434fc631101a2` |

## Work still required, in order

1. After an explicit quota-safe resume, inspect E525's retained exact boundary and measure the repeated planner observation work. Make one demonstrated change before another expensive public journey. Next unique writer would be `p3-engineer-efficiency-134`, with actual public start/finish guards.
2. Complete genuine public workspace plan/apply/verify. Exercise both platforms, old CLI upgrade, preserved global configuration/instructions, owned Harness environment and combined receipt/journal. New-origin activity schema/resource/external initialization adaptation is still pending; do not grandfather unknown shapes.
3. Complete ordinary-profile omission/live/unknown/lease/held-lock/unresolved/membership-drift negatives and transactional source/preimage rechecks. The caller cannot choose ordinary via empty acknowledgements or a profile label. Derive both authorized directory trees, nested/linked worktrees, invoking/registered/explicit/ambient roots and both platforms independently.
4. Retain separate `ordinary-terminal-v1` and `acknowledged-history-v1` profiles. Historical validation failure cannot fall back. Actual final retirement must use exact original factual anchors wherever historical records require them. Never fake acknowledgement facts for disposable tests or reuse old user facts for later/changed sessions.
5. Finish cleanup/backup/rollback composition and instruction-link relocation. Preserve runtime bytes/statuses, unrelated configuration and instruction bytes. Exact-content local/global policy equality and global verification precede authorized relocation/removal. No real installation or removal has occurred in this task.
6. Finish owned setup/toolchain/environment/cache/service recipes, exact CI lint and test-tier preflight, proportional dependency scopes, precise invalidation, scope-growth/cost checkpoint and implementation/CI/release readiness. Measure duplicate child/suite executions and orchestration time. Generic preparation/reuse support is intentionally incomplete.
7. Address all three pending checkpoint-time requirements above through discovery, manifest validation, phase admission, scratch integrity and regressions. Verify and preserve the later supplied tooling repairs before integrating the baseline-exception feature.
8. Migrate test fixtures coherently for mandatory new starts/admission. No caller legacy opt-out, fake status flags, budget resets or guard weakening. Replace excluded externally sourced selector fixture/provenance with genuinely synthetic generic coverage before claiming portable full-suite success.
9. Complete measured full AL verification and any justified redundancy pruning; full Harness lint/format/build/unfiltered tests; current Phase 3 scenario/demo; and bounded honest native acceptance. Historical native attempts E105 (Claude interrupted, no AL run) and E106 (Codex cleanup PermissionError) are limitations, not passes.
10. Sequential Builder finalization after a complete Engineer handoff only. For browser verification: Builder produces six new screenshots/metrics/EID, pauses broad root pytest, supervisor validates/copies/hashes/views all six, records visual/UX observations and acknowledges, then broad checks continue. Old screenshots are historical.
11. Fresh Architect review in a new independent context: original task/expected behaviors/current factual evidence, no implementation plan/pseudocode/prior findings/implementer conclusions. Separate comparison review then receives the complete approved plan/design. Explicit counterexample search and actual original/supplemental criteria remain required. Do not reuse the planning Architect as the fresh reviewer.
12. Finish ordinary guarded source approval. Only then refresh real activity inventory/acknowledgements/plans, apply and verify globals, perform reviewed link relocations, remove only attributed local AL resources except exact myharness, and rescan preserved history/configuration/global integrity. Offer further performance suggestions at the very end.

The current legacy issuer has a pre-existing fresh-report truthiness limitation for an empty/null `prior_resolutions`. Supervisor must independently require that key to be absent from final fresh reports; new profile tests enforce that rule. Do not claim the old issuer mechanically enforces the new contract.

## Local continuation artifacts — preserve outside Git

These ignored/external artifacts are required for exact same-machine continuation and are not contained in a remote clone:

| Artifact | SHA-256 / purpose |
| --- | --- |
| `/private/tmp/harness-p3-plan-expanded-v15.md` | `07aca9f0d324a0d7fdb95a843a67d32d65291365981fd96282e916ecac968143` — latest full plan. |
| `/private/tmp/harness-p3-design-expanded-v11.md` | `61988265e85fc0aa61cba64d466cb834d7c5d056303caf5d3749abbdecec61ae` — latest full pseudocode. |
| `/private/tmp/harness-p3-workspace-activity-resolution.md` | `c7d0d2a4c7d2342d59ce0e52fc753089a6c2871df77976134225ae6be5b5d678` — narrow ordinary/historical discriminator decision; Designer extension not required. |
| `/private/tmp/harness-p3-engineer-workspace-quota-checkpoint-v3.json` | `e94f704af4dcdf240b0dbac0cd50f706979433b701ab0b08be589d7f3d3e1e6f` — 18 exact latest source hashes/modes and terminal cleanup. |
| `/private/tmp/harness-p3-engineer-workspace-quota-hold.md` | `d0a8f1763e55864ad4e95da7ab7ec3fdea2741f84ea608b7b5364b4f18bd1118` — complete partial Engineer hold report. |
| `/private/tmp/harness-p3-workspace-e523/manifest.json` | `cdb192a1507dd13691b86cdd6fdd18935ce7e0486c31fc6666895744e26f0fb5` — retained failed real public journey artifacts. |
| `/private/tmp/harness-p3-supervisor-notes.md` | Detailed earlier supervisor chronology and source/evidence limitations. |
| `/private/tmp/harness-p3-behavior-obligations-v1.json` | `75b17cb171d0110e70de99bf32d7e5eb6c7293e41e198c33b3ff379a33d0a103` — Architect-authored current-task obligations, seven groups/eighteen scenarios, external only. |
| `/private/tmp/harness-p3-authored-behavior-report.md` | `ac2738a8ceb2f42746d159cc53e8062a9ea15002d89c2594636c1790041533b3` — actual authorship provenance. |
| `/private/tmp/harness-p3-behavior-observation-matrix.json` | Supervisor's scenario-to-evidence accounting; initialized pending, final current mapping still owed. |

The actual current run is legacy: its authored obligations stay external and must be independently enforced before approval. Do not backfill new authority or alter old packets. New runtime disposable tests establish the mechanical future contract; they do not upgrade this run.

The roadmap/agent diagrams are also saved in the private Page titled **AL and Harness Roadmap and Architecture**, linked from `docs/ROADMAP.md`. It describes current and future agent collaboration and coordinated installation; a diagram is not a shipped-feature claim.

## Commit scope and resumption caution

This is a checkpoint branch, with no merge, release tag, deployment or full source approval. Pre-existing Phase 2-only acceptance/demo/task changes remain local and uncommitted. Their README-only hunks are also left local. The checkpoint includes prior AL installer/performance work that this task depends on, plus current Phase 3 and subsequent work. Two distributions' external-project selector provenance and verbatim source fixture remain local/uncommitted to honor the user's no-external-project-traces requirement; they need synthetic replacement, not publication.

Local runtime/evidence, immutable captures, external acknowledgement material and source-specific diagnostics are intentionally not committed. A new remote clone cannot claim their authority. Obtain the original local artifacts for exact continuation; if they are unavailable, use supported AL recovery and recreate required evidence honestly rather than manufacturing lineage or historical facts.

The user explicitly requested this Git commit/push. Its HEAD/index transition can invalidate source-bound observations and packets. Reinspect actual currency after resume; do not edit baseline hashes, normalize fingerprints, or narrow away the original task to recover green. Keep all implementation paused until quota and resumption authorization permit it.

No fresh test suite was run for the checkpoint commit because implementation is quota-paused. A read-only `git diff --cached --check` reported extra blank lines at EOF in both distributions' `tests/task_attribution_helpers.py:33` and Claude's `tests/test_hook_batch7.py:196`. They were preserved with the unfinished source rather than silently extending implementation.
