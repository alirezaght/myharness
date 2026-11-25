IMPLEMENTATION_SUMMARY / CORE_IMPLEMENTED

Authored core handback for publication-core-001, session reliability-publication-20261125/run c82f62fb. All checks settled; writer intentionally remains ACTIVE for root to close. No push or post-rebase commit. This is not completed publication, formal approval, production readiness or completion of the remaining original mission.

1. Completed actual rebase of preserved checkpoint ded9054310d1f8ffe90736b192fcfaec557be3c9 onto fef2cd1ec5650c7fba5cf9f056302d105f5c64a5, yielding HEAD69eaee9faef865e3ffe6b02cb5919c30fc6cda20. Preserved upstream Knowledge/planning-session/service/per-handle process storage and single-run test consolidation while integrating R1–R5a. Current core corrections remain dirty and are explicitly inventoried below.
2. Kept upstream0019_investigation_sources and0020_planning_sessions; relocated checkpoint reliability SQL bodies to0021–0025. Known-history recognition supports common prefixes, original checkpoint prefixes and resumable main/reliability combinations. Validation precedes mutation and repeats under write lock. Descending atomic ledger relocation preserves original timestamps/data. Current complete databases need no write lock. Deferred-FK COMMIT failures roll back; upstream legacy transaction guard remains.
3. Constraint-preserving checked migration manifest binds exact packaged SQL and full managed schemas. Ledger structure is checked before reading rows: exact published declaration/columns/types/nullability/default/PK, no extra constraints/indexes/triggers. Attachment ownership follows tbl_name regardless arbitrary object name; TEMP shadow/managed triggers refuse. Literal sqlite_ filtering no longer hides sqliteXhook. Unrelated extension tables/triggers/indexes remain usable. No schema reconstruction, drop/backfill or ledger repair. A real SQLite progress interrupt after a completed relocation update proves full rollback and successful concurrent retry.
4. Retained CheckChannel handles have their own policy hooks, process/instance, storage allocation, deadline, completion callback and acknowledgement. Captured immutable ProcessContext is explicitly carried into threads. Callback execution validates current task/epoch/principal/exact process and active original claims before consumption; finishing happens afterward. Failures retain unknown liabilities. Queued setup plan defaults are admitted by normal self.dispatch only after the original callback resets context and outside controller/artifact/DB locks; no borrowed finished Attempt or nested transaction.
5. Sealed retry/exact six-token writer-finish preserve authenticated activation and exact captured launcher equality, fixed isolated interpreter, tested process restriction, finite helper methods and bound completion receipts. Control-FD presence only bypasses resident-service optimization; it grants no permission. Invalid transport still refuses. Authenticated controls avoid ensure_service and cache writes; ordinary main service/cache behavior remains. Added only finite retained_tree Git helper form. Automatic writer-finish evidence is not covered by that exact control method.
6. Restored real pinned complete helper in resident service namespace without qualifying service closure. Newly issued planning packets retain immutable analysis_observation associations. Historical rebind authenticates the exact original associated analysis and historical normative/context bytes; later same-spec analysis cannot authorize it. Missing/unbound/ambiguous/tampered history refuses with fresh-current planning guidance. Derived current issuance binds current analysis; old packets/pins are not rewritten.
7. Fixed verified runner's alphabetization of qualified IDs: execution filters selected-minus-omitted through actual validated collection order, retaining every selected node, host, isolation, grouping and budget guard. Real three-host lifecycle regression is causal red E30/green E31.
8. Added keyword-only allow_clone=True. Initial _system discovery and source/parent native security observations remain unchanged. Only subsequent optional reference/clone qualification is gated by eligible and allow_clone; ordinary metadata/custody/hash/fsync/finalization/owned cleanup remain common. Production defaults and write_captured unchanged. Only initial disposable test source-template construction requests partial(copy2,allow_clone=False). No security-observation caching/abbreviation or production speed claim.

CHANGED_FILES / FILES_CHANGED

Exact candidate2221-member hashes (includes tracked deletions and untracked authored source), status and whole-MR tracked diff are retained in:
- /private/tmp/reliability-publication-core-source-manifest-20261125.json — SHA256007549aa3647f52a941780eeb6f891138112a86123f5709834762566d444e8dd
- /private/tmp/reliability-publication-core-status-20261125.txt
- /private/tmp/reliability-publication-core-diff-20261125.patch

Core files/groups:
- harness/db.py; migration_schema.py; migration_compatibility.py; migration_schema_manifest.json; migrations0021–0025 — canonical upgrade/transaction and guarded history/schema ownership.
- scripts/generate_migration_schema.py — authoring-only in-memory generator, never runtime mutation. Reproduce with qualified Python: `python scripts/generate_migration_schema.py > harness/migration_schema_manifest.json`. E35 checks generate()==manifest and exact SQL tamper refusal; manifest records every canonical SQL SHA256.
- harness/execution_{bootstrap,check_channel,processes,operations}.py; harness/policy/{processes,resources}.py — snapshot resources, per-handle lifecycle/claims/queued setup and actual read-only classification.
- harness/policy/{control_launcher,control_qualification,control_git,control_sources_installer,control_sources_codex,control_sources_claude}.py — exact launcher/finite helper/source closure integration.
- Both AL platform scripts al_planning.py/al_planning_rebind.py/al_snapshot.py — exact issuance analysis reference and finite retained-tree control helper. Platform-specific startup behavior retained.
- al/installer/runtime_service.py/source_materialization.py; al/verification_plan.py; al/tests/conftest.py — actual complete helper, explicit ordinary choice and source ordering.
- tests/test_migration_compatibility.py/test_check_policy_storage_integration.py/test_agent_contracts_setup.py/test_task_check_recipes.py/test_policy_retry_control.py plus causal fixture alignment in listed status — real migrations, owning claims, callbacks, process and qualified helper tests.
- al/tests/test_source_materialization_ordinary.py/test_runtime_service.py/test_runtime_service_unit.py/test_verification_runner.py/test_writer_start_progress.py; Codex test_planning_rebind.py/test_standalone_papercuts.py — actual custody/drift/metadata, bounded timing diagnostics, collection lifecycle, cached-verifier fixture and historical association companions.
- Removed only duplicate host-neutral Claude test_agent_contracts.py/test_incident_sources.py, whose imports referenced helpers main already consolidated away; complete Codex assertions retained, native Claude suites untouched. Main-deleted duplicate execution decisions/events/writer-handoff files were not resurrected.

Candidate source inventory audit (not qualification):115installer/179Codex/182Claude entries match actual bytes, zero mismatches. Inventory fileSHA256: installer f614b7d19a966b841cef19df419f0faae688b9a704b522b4dcdb7226958f4696; Codex cea16b85b484824a603f6a667b6de1c12d6a3c30268c7bb4026117d749edda1c; Claude53d327d7e7992b8c1e28ada57e0db07fb8909ad6e9ad593bf2574fade5cf2492. Installer names are alctl.py+sortedinstaller/*.py; platform names are sorted scripts/*.py, paths relative to al; hashes are exact SHA256. Audit callgraph before regenerating these literals; hash agreement alone never qualifies behavior.

DEVIATIONS_FROM_PSEUDOCODE

- None outside actual Architect amendments. Later exact analysis-issuance association refuses unbound historical records instead of guessing same-spec history. Scoped explicit ordinary fixture choice follows actual materialization guidance. Narrow ledger/TEMP ownership correction follows actual ledger guidance. Final timing work remains unresolved rather than bypassing constraints.

VERIFICATION_RUN / VERIFICATION

All entries below are actual managed ledger executions in this run; historical source currency is not upgraded. Logs and key report/artifact identities are retained in /private/tmp/reliability-publication-core-evidence-manifest-20261125.json (SHA2566ee523ba16dda81894955706b4cfc02fe17bd5873345f6ada84ba708766026a7).

E1 migrations001 FAIL2/PASS71 — transaction-owner fixtures. E2 lint FAIL16. E3 migrations002 FAIL3/PASS70 — complete epoch identity/WAL fixture prerequisites. E4 policy001 FAIL12/PASS32/SKIP1 — obsolete source maps.
E5 migrations003 PASS74. E6 lint FAIL3. E7 lint PASS/countsunknown. E8 policy002 FAIL12/PASS39/SKIP1. E9 representative001 FAIL1 — exact launcher equality. E10 representative002 PASS1.
E11 policy003 PASS53/SKIP1. E12 setup002 FAIL3/PASS110/SKIP4 — caller/background observation mix, immutable-root mutation and invalid dot-scope fixtures. E13 lint FAIL1. E14 lint PASS/countsunknown. E15 setup-focused PASS3. E16 fast001 FAIL/countsunknown — Claude duplicate collection, child diagnostics lost under old private TMPDIR; public log retained. E17 format FAIL3. E18 SQLite capabilities PASS6 on actual3.54.1/Python3.13.18. E19 format PASS/countsunknown,775files.
E20 fast002 FAIL(error1 failed21 passed2887 skipped0) — planning/service/unit causes. E21 focused001 FAIL(error1 failed30 passed76 skipped0) — bounded diagnostic red. E22 focused002 overallFAIL(error0 failed1 passed110 skipped0),26timingviolations despite all110functional passes.
E23 lint PASS/countsunknown. E24 format PASS/countsunknown. E25 focused003 overallFAIL(error0 failed1 passed111 skipped0),34timingviolations; all111functional pass. E26 representative003 PASS1.
E27 installer/control001 FAIL(error0 failed10 passed497 skipped0),1699.6s;10cached-verifier fixture setup failures and223timingviolations retained. E28 whole-file service001 FAIL(error0 failed2 passed15 skipped0), genuine source-order failure plus3budgets. E29 writer-progress001 overallFAIL(error0 failed1 passed11 skipped0), all11functional pass but16.5s real case violates budget.
E30 runner001 FAIL(error0 failed1 passed32 skipped0), actual three child lifecycle failures demonstrate alphabetization. E31 runner002 PASS(error0 failed0 passed33 skipped0). E32 service-profile002 overallFAIL(error0 failed1 passed17 skipped0),4timingviolations; all17functional pass, cProfile overhead explicit. E33 lint PASS/countsunknown, subsequently stale.
E34 migrations004 FAIL2/PASS74 — genuine pre-fix ownership red: ledger18→25 in both malformed cases, arbitrary ledger trigger7notes. No DB guard changes before red; issue63 recorded by root.
E35 migrations005 PASS85/28.99s — all supported histories/data/current-readonly/reproducibility/ledger+attached+TEMP guards and real after-update rollback/concurrent retry. Later ordinary-test formatting changes conservative full-scope currency only.
E36 materialization001 PASS(error0 failed0 passed67 skipped0)/20.8s — both modules, actual Darwin clone/ACL plus ordinary choice/metadata/security-drift/custody/races/owned cleanup/fsync/malformed refusal. No skip/production speed inference.
E37 representative004 PASS1/42.09s — actual repository_identity/top/scope_identity/index/tree/read_blobs helpers closed with realPIDs; exact receipt/helper_count, tested main/helper darwin-deny-process-fork-v1 binding, attempt settled/returned, own process boundary/claims closed, no canary content/hash in helper facts. This qualifies that exact invocation only.
E38 original default-parallel focused004 overallFAIL(error0 failed1 passed111 skipped0)/132.5s — all111functional passes;34timingviolations remain. Original4files/all-tier/defaultparallel selected; no acceptance substitution.
E39 current lint PASS/countsunknown. E40 current format PASS/countsunknown,775files. New ordinary test received scoped formatting only after E38; runtime/source inventories unchanged. AL vendor style exclusion remains as upstream, not a new exception.

Current setup003 and fast003 are NOT RUN; final broad policy/control/source-closure/dashboard/packaging/provider/hosted checks remain pending. Earlier results are retained execution facts, not a claim of current VALID whole-candidate coverage. No source edits occurred during checks. All command subprocess cleanup retains owned-process-group-only facts and descendant_settlement_unproven; absent group/PID never proves universal closure.

E38 MEASUREMENTS AND RETAINED ARTIFACTS

Report /private/tmp/publication-al-focused-repair-004/report.json. Three service stage files (source-copy/bootstrap/first-start seconds respectively):
- /Users/alirezaghiastabari/.local/share/harness/managed-storage/allocations/4317c7f1661da82575f1d80d3c8f9bbb/pytest-tmp/service0/stage-profile.jsonl — SHA256818b10dfae7688f0418c94411689eb184bf8fb9992238c02749e61dca15f6380;13.016/24.403/19.536.
- /Users/alirezaghiastabari/.local/share/harness/managed-storage/allocations/2477889e7d1e5d1e7933f0cf0e774786/pytest-tmp/service0/stage-profile.jsonl — SHA25654d732681e0236bb48f16199f31207c3aba1d4919250eb53a3f60a5809dc9abf;12.688/24.694/19.929.
- /Users/alirezaghiastabari/.local/share/harness/managed-storage/allocations/6e0246d593d8910fa55b23aeaae2ace1/pytest-tmp/service0/stage-profile.jsonl — SHA25648bb2e39d0933d9857ea709e34f58d12790a2b98053320a8eca30274a4876d5d;12.648/24.900/20.349. Later distinct-runtime bootstrap16.372/start12.207.

Aggregate file timings: service256.1s, planning_rebind189.8s, standalone_papercuts142.7s. Repeated module setup/real bootstrap/start remain costly; ordinary option did not establish a passing budget. No speculative production optimization. Bounded stage JSONL retained; expensive cProfile removed before E38. E32 exact profiled source /private/tmp/reliability-service-profile-source-E32.py SHA2560f473f80285bea7e73075a37aa8d0d6671e334a6403706564eefd3cb27240488; original pstats and managed storage retained. Whole-file diagnostic is not original acceptance substitution.

LEFT_FOR_BUILDER

- Packaging/resource integration: authority_snapshot already includes migration modules+JSON/control_launcher; prove actual installed wheel/import/resource closure and add missing explicit migration_schema_manifest.json package inclusion if needed. Do not mutate old pins.
- Current merged dashboard/static wiring, meaningful desktop/narrow Chrome functional and visual validation, docs/source inventories/closed schema parity and remaining useful integration support.
- Document canonical migration history/refusal/no manual ledger-edit path and memory-only generator/exact SQL identities, historical analysis refusal/fresh-current route, exact control/closure limitations and real evidence status.
- Complete relevant stable-source shared fast003 and deliberate slow/control, setup003, policy/migration/snapshot/packaging/browser verification as warranted; preserve all selection/host/assertion/budget semantics and failures. Do not blindly repeat expensive unchanged suites before measured cause/support changes justify it.
- Timing is active operational verification work:5s/test60s/file limits remain failing, no exemption/fast-tier/hidden setup-clock workaround. Root may sequence independent support while responsible measured diagnosis continues; full proof cannot call E38PASS.

OWNERSHIP_CONFLICTS

None. Single source writer; root/warm roles remained read-only or metadata-only. No DB/auth/provider/global install/pin/security widening or Atlassian write performed by this writer.

CONCERNS / UNVERIFIED

- Publication held by real verification/support debt, not pending generic user permission: timingFAIL, packaging/Chrome/independent review/final proof pending. Original R5b–R11 still unimplemented subsequent increments; R5a checkpoint is not full mission completion.
- Stored whole-MR merge_base in this run remains2ad8bed and cannot be publicly retargeted after writer registration. Root's actual Architect calls for a distinct verification/publication run bound to completed rebasedHEAD/currentmain. No evidence/proof/budget transplantation or clearance of oldR4 debt. R4 RR1pending/used2total2/autoauthorizations0 historical obligations remain, not reset here.
- E11 Linux bubblewrap test skipped. E12 opt-in signed-in Codex/Claude/native exact0.164 smoke cases skipped. No hosted CI/provider/general native qualification asserted. Helper receipt is exact method/profile evidence; service/status/publication/group absence grants no broader closure.
- Prior automatic review rejected combined unused-import cleanup/assertion reduction; rejected edits never executed. Accepted narrower import-only action left actual validate_reference call and full owner.started-and-owner_done assertion intact. Root reported rejection to user; no remaining permission question and not labelled AL runtime defect.
- Root-owned qualified toolchain receipt /private/tmp/reliability-toolchain-20261125/receipt.json; locked sync succeeded; no tests claimed before replacement. All SQLite targets disposable; database reference respected.

OPEN_QUESTIONS

No unresolved material product/architecture choice blocking authored handback. Remaining named verification/performance/support obligations above are not passes.

HANDOFF_RECORD

/public actual structured record input: /private/tmp/reliability-publication-engineer-handoff-record-20261125.json. Root receives public storage receipt separately. No writer finish by Engineer.
