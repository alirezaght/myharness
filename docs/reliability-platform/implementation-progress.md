# Reliability implementation progress

## R1: persisted operation outcomes

The approved first reliability slice adds trusted, metadata-only operation observations and scoped incident reads to the existing execution dashboard. The selected execution view now has an **Operation incidents** section backed by the persisted incident list and detail endpoints. Search is literal, categories are closed, Older uses the API cursor, and pending observations remain a separate **Outcome not recorded** list. The UI has no retry, resolve, approval, or activation controls.

The page states that these are observed operation outcomes without root-cause assessment or approval authority. It also displays that coverage is limited to operation dispatch; transport and epoch rejections, assignment waits, historical backfill, and asynchronous completion classification remain uncovered. Read failures clear incident content and show a fixed diagnostic.

Trusted execution snapshots now include the explicit reliability source closure (`__init__.py`, `contracts.py`, `identity.py`, `store.py`, and `query.py`) in the manifest hash. Existing immutable snapshots are reused without modification; a changed closure produces a versioned successor.

R1 does not add Knowledge publication, provider telemetry, retries, incident resolution, improvement proposals, evaluation promotion, or changes to runtime authority. The wider mission remains future work. Verification and independent review are recorded separately from implementation status.

## Earlier R1 support verification history

The records below are retained from the earlier support pass; they are historical ledger outcomes, not a fresh verification of the later repaired source. Stale passes and the earlier browser-regression failure remain in the ledger unchanged.

- E11 snapshot closure: PASS, 22 tests.
- E23 persisted incident UI in desktop and narrow Chrome: PASS, 1 test. Screenshots: `/private/tmp/al-r1-browser-002/test_persisted_operation_incid0/incident-shots/operation-incidents-1280x800.png` and `/private/tmp/al-r1-browser-002/test_persisted_operation_incid0/incident-shots/operation-incidents-390x844.png`.
- E24 operation, activity, event, epoch, dashboard, and migration regressions: PASS, 50 passed and 4 skipped. The skip count is retained as skips and does not qualify those cases.
- E26 JavaScript syntax check: PASS; E27 backend lint: PASS; E22 support lint: PASS.
- E28 reliability core: PASS, 38 tests.
- E25 browser regressions recorded the prior 24-resource budget failure. The bounded contract was updated to allow 27 resources, require both reliability assets, and bound incident-list requests; E29 reran the browser regressions and passed all 3 tests.

These checks do not constitute the independent review or final AL approval. The broader reliability mission remains in progress.

## R1 diagnostic resolution status

The fresh independent diagnostic review found four frontend findings. All four have been repaired and the red/green evidence is retained; this is scoped diagnostic resolution, not formal AL approval. E39 is the retained red navigation run (52/57 and 100/101 scenarios); the exact same command in E42 passes 57/57 and 101/101, with no missing records. E36 and E42 exercise the overlapping list/detail request paths and focus/disclosure preservation. The reviewer also inspected the retained E42 desktop and narrow screenshots and reported no apparent clipping, horizontal overflow, or material alignment issue. The R1 review-resolution record documents each finding and the remaining qualification gaps.

## R2 correlated execution timeline

The R2 increment adds a read-only timeline to the existing execution view. It correlates persisted R1 operation outcomes with owned-process lifecycle observations, validated semantic report references, and authorized retrieval references in an exact task/execution/epoch scope. The list is cursor-paged, process detail is separately scoped, and unavailable measurements and coverage gaps are explicit. It does not add approval authority, infer root cause, backfill historical facts, or qualify historical comparisons.

The user instructions and HTTP contract are recorded in [user-guide.md](user-guide.md) and [r2-api-contract.md](r2-api-contract.md). They are based on the current dashboard launcher, strict query parser, projection, and timeline client. R2 supports operation dispatch, owned-process lifecycle, validated semantic links, and authorized retrieval references. Early transport rejections, historical backfill, durable process recovery, and native-child observation are out of coverage. Tokens and cost are unavailable; duration is monotonic-clock measured only when observed.

### R2 evidence history

Current protected run: session `reliability-r2-active-20261119`, run `0d32430a`. These are actual ledger records from that run; later UI and test changes mean earlier source-area passes are not all current full-scope proof. No checks were rerun for this documentation-only update.

- E13, R2 core: PASS, 36 tests. E14, focused regressions: PASS, 72 passed and 4 skipped. E15, R1 core: PASS, 39 tests. The backend source was unchanged after those runs, but these results do not prove the later UI/test tree.
- E11 and E17 JavaScript checks: PASS; recorded counts are unavailable. E18 lint and E19 format: PASS; recorded counts are unavailable. Treat them as historical check outcomes, not fresh verification of current documentation or later source edits.
- E20, timeline browser on the distinct `r1-browser-003` fixture: PASS, 2 tests, including live process detail. E21, browser regressions on the distinct `browser-regressions-002` fixture: PASS, 5 tests. The retained screenshots were inspected at 1280×800 and 390×844; both show the incident controls and results, with no apparent clipping or horizontal overflow. Desktop content was dimmed by the existing stale-state styling after the fixture's forced SSE disconnect; no new layout defect was found.
- Initial failures E1, E2, E4, E10, and E16 remain preserved as failures. They are not converted into passes by later records.

These results establish the authored implementation and the named checks at their recorded source state. Independent current-source assessment, complete relevant service/consumer/check discovery, broad CI, native token/cost/child-observation qualification, formal AL review/approval, and the remaining roadmap phases are still pending. The full mission continues through policy, contracts, incidents, improvements, evaluation, Knowledge, recovery, and providers; R2 is not full-mission completion.

The R2 diagnostic finding R2-FRESH-001 is resolved in the inspected repair: semantic references are selected by exact event kind and validated field. The retained repair checks are E25 (66 passed, no failures/errors/skips), E26 (84 passed), and E27 (12 passed); these resolve the scoped privacy finding but do not replace current-source broad regression, formal review, or mission completion.

## R3: runtime policy, budgets and qualified control paths

R3 extends the existing execution boundary with closed policy documents, transactional budget pools and one-use approval capabilities. Applicable scopes intersect with inherited policy and existing Harness authority; explicit deny wins. Server-side handlers derive principal, task, epoch, resource and effect scope. Network, database, production and unsupported delegation restrictions refuse when the current runtime cannot enforce them.

The policy read view is scoped to task, execution and epoch. Decision and capability histories page independently in groups of 25, and capability coverage reports partial versus complete history. Pool rows distinguish consumed and reserved units from unavailable usage. Mutation and retry units count admitted invocations; they are not file or syscall counts. Token, cost and native-child measurements remain unknown. Protected developer decisions and their acknowledged canonical authorization deliveries are required for policy installation, capability issue/revoke and workflow registration; the local dashboard does not establish named-user identity.

Coordination uses only the existing project SQLite database. Sticky resource-domain enrollment, known liability checks and dependency claims persist across policy/budget changes. Conflicting or unknown resource/process obligations remain fenced until the supported closure proof exists. Untouched legacy operations and independent databases are not represented as coordinated. Charged effects and capability uses are not refunded on failure, cancellation or no-op; only a fenced, proven-not-started reservation may release capacity.

The only currently qualified sealed control methods are `sealed-al-retry-v1` and `sealed-al-writer-finish-v1`, for inspected immutable runtime/pin/Git inputs on macOS with separately qualified main/helper no-fork profiles. They do not qualify arbitrary commands, native/provider process trees, general strict deadlines, Linux/container execution, or hosted behavior. In a separate disposable Docker probe, E109 first exposed an incorrect image identity; corrected E110 reached the container but failed because the host denied the required namespace operation. No Linux qualification was obtained and no security setting was relaxed.

The new operator/API details are in [r3-api-contract.md](r3-api-contract.md) and [user-guide.md](user-guide.md). The retained Architect and Designer captures are listed in [planning/provenance.json](planning/provenance.json); the R4 and later planning artifacts are plans, not implementation evidence. At that earlier status point, R3 source reinspection was still pending. The subsequent E140 diagnostic reinspection and resolution are recorded below; complete mandatory verification/coverage, formal AL review/approval and remaining mission phases remain pending.

### R3 finalization evidence and limits

The retained history is not overwritten: E111 and E114 are the first fixture/lint failures. E112 is the fresh fixture-area pass (7 passed) after repairing the two invalid `read_file` fixtures, the two successor-principal fixtures, and one duplicate root-SessionMeta fixture in the owned fencing file. The unused import found by E114 was removed; E115 is the subsequent lint PASS. E113 format passed before that import-only cleanup. The owned semantic fixture pass is retained as evidence, not a claim that the broader suites passed.

- E116, observation regressions: **FAIL, 2 failed / 121 passed**. The two failures were in `tests/test_reliability_timeline.py::test_real_foreground_al_dispatch_links_its_process_without_changing_result` and `tests/test_reliability_timeline_fencing.py::test_active_runtime_retirement_rejects_terminal_process_callback`; both found duplicate canonical root SessionMeta fixtures. They remain actual history; the later narrow fixture repair is recorded by E122 below.
- E117, authority regressions: **PASS, 85 passed**. This includes both successor-authority parameter variants and the approved-before-first-reservation recovery race.
- E112 is the fixture check's PASS before the later removal of one unused import; E115 covers that final Python-file edit. E113 checks formatting on the prior import-only state. E116/E117 were run after the import was removed. No test behavior was weakened.

Ordinary repository `read_file` now cooperatively checks its existing bound around descriptor acquisition, bounded hash reads/`pread`, source validation, descriptor cleanup and response decoding. Legacy calls without an active policy attempt are unchanged. These checks cannot interrupt a syscall already in progress; special `harness:`/`artifact:` handlers and general process/native deadlines are not included. `apply_patch` retains its per-file pre-mutation checks.

No Linux qualification was obtained: E109 used a wrong image identity and E110, with the corrected identity, failed at a host namespace permission boundary. The container was removed and no security setting was relaxed. Independent source review, complete relevant coverage/CI and formal AL approval remain pending. No native/provider or arbitrary-command qualification is inferred from skipped tests or the two qualified sealed-control methods.

### R3 read deadline correction evidence

- E118 is **INFRA**, not a causal red: disk exhaustion occurred during pytest session finish and public ledger commit. The public reconciliation record and root's capacity-cleanup audit are archived with hashes in [r3-evidence/manifest.json](r3-evidence/manifest.json). Capacity recovery removed 38 known settled disposable basetemp roots only; their XML, logs, reports, source/review snapshots, browser artifacts and pins were preserved. This was an audited one-time cleanup, not an automatic retention feature.
- E119 is the genuine causal red: **FAIL, 10 failed / 22 passed**. The controlled real-handler cases returned pages after the bound. E121 is the later deadline regression: **PASS, 32 passed**. Exact red and green XML are archived as `E119-red.xml` and `E121-green.xml`; their source paths and SHA-256 hashes are in the evidence manifest.
- E120 deadline lint: **PASS**. E123 deadline format: **PASS**. E122 observation regressions: **PASS, 123 passed**, including the two repaired canonical-root timeline fixtures and their existing fencing assertions.
- E116 remains preserved as the earlier observation failure, and E117 remains the historical 85-pass authority result; neither was rerun as part of this documentation-only update. E112/E115 fixture/lint history is unchanged.

This cooperative boundary detects elapsed-budget expiry between bounded file-read steps and before returning decoded content. It does not preempt a kernel syscall already in progress or qualify strict process/native/provider deadlines. E118/E119/E121/E122 are distinct infrastructure, red, deadline-green and observation-green records.

### R3 fresh review, repair and support evidence

A scoped independent diagnostic assessment was authored against frozen candidate `/private/tmp/al-r3-frozen-review-002`. The assessor reported three BLOCKING source-derived counterexamples for R3-02/R3-06/R3-I2 (immutable selected parent ceilings), R3-08/R3-13 (patch mutation after deadline expiry), and R3-03/R3-16 (repository-audit reads bypassing claims). The assessment was not an executed reproduction or formal AL verdict. Its exact source/run/report provenance and each finding are retained in [r3-independent-review.md](r3-independent-review.md). The findings were later reproduced in E124, and the selected-ceiling, patch-boundary, audit-claim and adjacent read-classification repairs are described there. They remain authored repairs pending actual independent reinspection; this text does not clear or adjudicate the BLOCKING findings.

- E124 · `r3-review-repro`: **FAIL, 12 failed / 35 passed**. This is the retained causal reproduction for all three findings. E127 · `r3-review-repro`: **PASS, 53 passed**, was an intermediate repair population before adjacent source-route classification; it is historical and not final-source proof.
- E132 · `r3-review-repro`: **FAIL, 16 failed / 67 passed**, captured the additional unknown-`harness:` fallthrough and descriptor-backed diagnostic classification cases. E134 · `r3-review-repro`: **PASS, 83 passed**, is the matching final reproduction-area result after those route fixes. Exact E124/E127/E132/E134 XML is archived with original source paths and SHA-256 hashes in [r3-evidence/manifest.json](r3-evidence/manifest.json).
- E135 · `r3-review-core`: **PASS, 124 passed**. E136 · `r3-review-files`: **PASS, 27 passed / 4 skipped**; skips remain skips. These retained XML records are associated evidence, not proof of formal approval or exhaustive qualification. E133 lint and E137 format passed on the final authored source; E134/E135/E136 were current recorded support checks. E125/E126 and E129–E131 remain their original failures or historical/stale passes; none is overwritten.
- The policy view now labels `maximum` as **Shared pool limit** and says selected/inherited immutable ceilings can further constrain an operation without resetting shared counters. The API field and numeric value are unchanged; no role-specific aggregate was invented.

The implementation keeps the shared pool row/counters distinct from per-operation effective ceilings. Patch deadline checks run before subsequent mutation steps after blocking work; unconditional temporary-file cleanup remains, already-completed target effects are not rolled back, and charges remain consumed. Coordinated live repository audits use a conservative repository-wide claim and refuse before source/Git traversal because their execution boundary is not qualified. Exact named control-resource and descriptor-diagnostic classification, including file claims and current time-qualification limits, is documented in the user guide and API contract.

The documentation and screenshot support checks do not independently re-review the production source. E118 remains infrastructure-only, E119 the earlier genuine read-deadline red, E121/E122 their later repair history, and E116/E117 their original fixture/authority history. Linux/container and hosted/native/provider limits, strict general process deadlines, formal AL review, complete mandatory-check coverage and remaining R4+ mission work remain pending.


- E138 · `r3-review-js`: **PASS**, `node --check harness/dashboard/static/policy.js` (counts unknown), after the shared-pool label/explanation UI edit. E139 · `r3-review-browser`: **PASS, 2 passed**, actual Chrome at 1280x800 and 390x844. Both functional JSON records and all four desktop/narrow full-view and capability screenshots are archived in `r3-evidence/manifest.json` with SHA-256 hashes. The functional records show independent list/detail and capability paging, focus/disclosure preservation, 106 decisions and 105 capabilities traversed, section-contained controls, no horizontal overflow and safe output. Visual inspection found no clipping, alignment, wrapping or control issue at either width. These checks cover the UI change; they do not constitute full source review.


### R3 E140 diagnostic reinspection (historical frozen003 evidence)

The actual retained independent report [R3-reinspection-001.md](r3-evidence/R3-reinspection-001.md) reports R3-FRESH-001/002/003 fixed in `/private/tmp/al-r3-frozen-review-003`, with no new counterexample in the inspected scope. E140 · `r3-review-repro` is **PASS, 83 passed, zero failures/errors/skips**, marked VALID at freeze; its original XML and source hashes are in the R3 evidence manifest. This supersedes the earlier pending-reinspection wording for those three scoped findings, while leaving formal AL review/approval, exhaustive source/check coverage, native/provider qualification and the full mission open. E140 is frozen003 R3 evidence, not current full-source R4 proof. E124/E132 reds and E127 intermediate pass remain unchanged.

## R4 typed agent exchanges

R4 adds the closed stdlib-only version-1 exchange companion at all ten current producer/consumer families. This is an authored R4 increment of the full mission, not full mission completion. See [r4-api-contract.md](r4-api-contract.md) for the object and consumption rules and [r4-implementation-inventory.md](r4-implementation-inventory.md) for the producer, authoritative consumer, focused test and package/source map.

The common envelope binds actual run/session/task/execution/subtask, graph/task revisions, producer, epoch and generation where they exist. Pre-execution and standalone values carry explicit applicability instead of fabricated identities. Required immutable references resolve to exact server-selected bytes; the validator consumes the same cached buffer it checked. Family statuses are closed. Safe errors expose a fixed code, family/version, bounded field pointer and repair guidance without echoing submitted content.

All ten actual family seams are wired: requirement-analysis from InterviewEngine and effective amendment lineage; architecture-proposal from ArchitectureDesigner; design-decision from the recorded decision registry; implementation-plan from the existing planning report/admission path; task-assignment from the authenticated generated writer receipt; implementation-result from explicit AL/Harness submission and authored-record consumers; verification-result from the actual evidence ledger and specialized validators; failure-report from operational issue updates; escalation-request from real integration blockers; and final-delivery from publication readiness plus existing publication validators. Existing specialized checks remain authoritative. Internal reasoning/general artifacts are unstructured, and the ten companions do not create an HTTP contract service.

Immutable assignment and output exchange preserve actual legacy uncertainty and can state `immutable_snapshot_validated` with `mutable_source_not_checked`. Actual mutable source consumption is separately guarded by R3 claims on the existing dispatch Attempt, predecessor and pending-native launch checks, reservation before source hashes, claims-through-use and start-time authority/receipt revalidation. It reports `coordinated_source_consumption` only when those checks establish it. `legacy_uncoordinated` does not mean safe or complete. General writer-start, task-agent/task-authored lifecycle operations and outer native process boundaries remain unqualified. An additive launch migration does not reinterpret historical unknown/null rows as no-start. No new all-lifecycle qualification or R5+ gate is introduced.

### R4 verification history

All E1–E27 outcomes from the actual Engineer ledger are retained below. Later fixture and lint corrections do not erase causal failures. The plan's current-source check obligations after the Builder documentation/config changes are run separately with fresh output paths where a prior path had already been used.

| EID | Area | Result |
| --- | --- | --- |
| E1 | `r4-lint` | FAIL, counts unknown |
| E2 | `r4-harness-contracts` | FAIL, 12 failed / 3 passed |
| E3 | `r4-harness-contracts` | FAIL, 4 failed / 13 passed |
| E4 | `r4-al-codex` | FAIL, 1 failed / 56 passed |
| E5 | `r4-lint` | FAIL, counts unknown |
| E6 | `r4-harness-contracts` | PASS, 18 passed |
| E7 | `r4-al-claude` | PASS, 58 passed (historical source) |
| E8 | `r4-lint` | FAIL, counts unknown |
| E9 | `r4-lint` | PASS, counts unknown |
| E10 | `r4-preexecution` | FAIL, 2 failed / 48 passed |
| E11 | `r4-lint` | FAIL, counts unknown |
| E12 | `r4-mutable-protocol` | PASS, 49 passed |
| E13 | `r4-launch-protocol` | PASS, 5 passed |
| E14 | `r4-handoff-protocol` | FAIL, 1 failed |
| E15 | `r4-handoff-protocol` | FAIL, 1 failed |
| E16 | `r4-handoff-protocol` | FAIL, 1 failed |
| E17 | `r4-handoff-protocol` | PASS, 1 passed |
| E18 | `r4-mutable-protocol` | FAIL, 4 failed / 46 passed |
| E19 | `r4-mutable-protocol` | PASS, 50 passed |
| E20 | `r4-launch-protocol` | PASS, 5 passed |
| E21 | `r4-harness-contracts` | PASS, 21 passed |
| E22 | `r4-preexecution` | PASS, 50 passed |
| E23 | `r4-al-codex` | PASS, 58 passed |
| E24 | `r4-handoff-protocol` | PASS, 1 passed |
| E25 | `r4-lint` | FAIL, counts unknown |
| E26 | `r4-lint` | PASS, counts unknown |
| E27 | `r4-format` | FAIL: one preserved planning Markdown file only; 670 other files formatted |

E2/E3, E4 and E10 exposed real fixture/context/claim-wiring gaps; their repairs kept the prior assertions. E14 had no declared evidence area, E15 named a nonexistent pre-patch scope, and E16 exposed shared-reader lock append access under the existing sandbox. A separate read-only probe diagnosed that retained sandbox boundary; it was not a ledger pass or containment qualification. E17 and E24 later passed their actual lifecycle scope. E18 exposed missing prepared-source protocol tags; E19 passes all 50 cases. E25 was fixed with minimal import/blank-line edits in `agent_exchange.py` and the handoff test; E26 passed. E27 alone failed because formatting would alter lines 76–79 of the preserved `r3-r2-integration-designer-addendum.md`; only that captured planning directory is excluded from Ruff formatting. This formatter-only exclusion does not change lint or production Python formatting.

### R4 first-finalization verification history (E28–E50)

This first sequential batch predates the return1 repair and its fresh current checks are listed below. E29 is the original registered Codex-selector failure and is preserved; its exact source paths were missing, so pytest ran zero tests. Root separately compared the registration against the source-verified E23 recipe and admitted the canonical `al/codex/skills/al/tests/` selector under fresh output paths. E48 is an infrastructure interruption before pytest; the corrected selector's E49 pass followed after capacity was restored.

| EID | Area | Result |
| --- | --- | --- |
| E28 | `r4-al-claude` | PASS, 58 passed |
| E29 | `r4-al-codex` | FAIL, exit 4; missing selector files, zero tests ran |
| E30 | `r4-claude-corruption` | PASS, 61 passed |
| E31 | `r4-consumer-regressions` | FAIL, 9 failed / 17 passed / 4 skipped |
| E32 | `r4-control-regressions` | PASS, 24 passed |
| E33 | `r4-delivery-regressions` | PASS, 4 passed |
| E34 | `r4-handoff-protocol` | PASS, 1 passed |
| E35 | `r4-harness-contracts` | PASS, 21 passed |
| E36 | `r4-harness-snapshot` | PASS, 11 passed |
| E37 | `r4-installer-closure` | PASS, 18 passed |
| E38 | `r4-launch-protocol` | PASS, 5 passed |
| E39 | `r4-lint` | PASS, counts unknown |
| E40 | `r4-migration` | PASS, 18 passed |
| E41 | `r4-mutable-protocol` | PASS, 50 passed |
| E42 | `r4-native-startup` | PASS, 2 passed |
| E43 | `r4-parity` | FAIL, 1 failed |
| E44 | `r4-policy-regressions` | PASS, 56 passed |
| E45 | `r4-preexecution` | PASS, 50 passed |
| E46 | `r4-publication-regressions` | PASS, 1 passed |
| E47 | `r4-worker-regressions` | PASS, 19 passed |
| E48 | `r4-al-codex` | INFRA, storage reserve unavailable before pytest started; zero tests |
| E49 | `r4-al-codex` | PASS, 58 passed |
| E50 | `r4-format` | PASS, counts unknown; 648 files already formatted, before return1/docs updates |

E31 is a genuine current regression result, not a selector problem. The retained read-only diagnosis in [r4-evidence/E31-readonly-engineer-diagnosis.md](r4-evidence/E31-readonly-engineer-diagnosis.md) attributes seven direct `test_task_tooling.py` failures to incomplete fixtures that cross into execution state, one scoped-setup fixture to a missing `dependencies` row before polling, and one continuity test to direct byte validation without its owning Attempt. It also identifies two source-backed continuation gaps for follow-up: the no-policy queued-effect branch in `execution_operations.tick` calls `prepare()` without admission, and `al_command` can reuse an outer Attempt whose task/resource intent differs from auto-preparation. These require a source repair and focused proof; this support pass did not patch product code or alter the tests. All four E31 skips remain skips and qualify no behavior.

E43's parity assertion found two files present in both platform script trees but absent from the union of `shared_files` and `different_content`: `al_policy_retry.py` and `al_retry_control.py`. Their classification must follow actual byte/content and adapter behavior; the names alone do not establish whether they are shared or different. The repair and its proof remain pending. E29 is a test-registration failure with no product result, while E31 and E43 are actual failing tests. E48 did not execute pytest: the ledger returned `storage_reserve_unavailable` before process start and created no XML. Root then restored the protected installer's 5 GiB free-space prerequisite by removing six exact obsolete settled basetemps after preserving their XML/log hashes (audit `/private/tmp/al-reliability-disposable-cleanup-20261120-003.jsonl`); no reserve policy, pin, allocation, or global setting was changed. The corrected recipe then passed at E49. E28–E49 logs are retained by the AL ledger; XMLs and the E31 diagnosis are archived with hashes in [r4-evidence/manifest.json](r4-evidence/manifest.json) where XML exists.

Executed pytest runs E28–E47 and E49 also emitted the ledger storage state `RETAINED · descendant_settlement_unproven`. Pytest exited and its owned process group was absent, but that is not proof that all descendants in the process tree settled. This uncertainty is retained separately from the test exit/counts and does not qualify containment. E48 is a distinct pre-execution INFRA event. The current batch uses the R4 authored source frozen before Builder support edits; Builder documentation and manifest updates occurred after individual focused runs and do not retroactively change their results.

E7/E23 or focused passes do not establish hosted CI, complete service/check discovery, formal approval, general native/provider qualification, complete lifecycle coverage, or mission completion. E27 remains a failure history after its scoped formatter configuration repair. E49 is a post-capacity Codex-area pass on the pre-return1 source; E48 remains its preserved infrastructure interruption. E50 is a pre-return1 formatter pass and is not current after later docs edits.

### R4 return1 setup ownership repair

The return1 Engineer repair assigns automatic dependency setup to a separate effect dispatch. `auto_prepare()` validates bounded task/revision metadata and queues concrete repository-relative requests without reading target dependency manifests or running an installer under the outer AL Attempt. `Queued` means pending only. When no Attempt is ambient, `tick()` revalidates the exact retained setup Attempt or dispatches a fresh `environment_operation`; `prepare()` requires the current Attempt to match the execution, principal, task and revision before target reads. Poll/cancel does not erase unqualified legacy liability or prove process closure. The exact source hashes and authored report are archived in [r4-evidence/repair-source-manifest-001.json](r4-evidence/repair-source-manifest-001.json) and [r4-evidence/E51-66-repair-engineer-report.md](r4-evidence/E51-66-repair-engineer-report.md). The repaired source candidate is the internal frozen003 handoff, not an independent review or approval.

| EID | Area | Result |
| --- | --- | --- |
| E51 | `r4-setup-protocol` | FAIL, 11 failed / 17 passed / 4 skipped; causal pre-fix source red |
| E52 | `r4-lint` | FAIL, counts unknown; local test style/unused-variable errors |
| E53 | `r4-lint` | PASS, counts unknown |
| E54 | `r4-format` | PASS, counts unknown |
| E55 | `r4-parity` | PASS, 1 passed |
| E56 | `r4-setup-protocol` | FAIL, 1 failed / 29 passed / 4 skipped; root-task-scope fixture refusal |
| E57 | `r4-lint` | PASS, counts unknown |
| E58 | `r4-format` | PASS, counts unknown |
| E59 | `r4-setup-protocol` | PASS, 30 passed / 4 skipped; later stale under broad test-tree scope |
| E60 | `r4-consumer-regressions` | PASS, 26 passed / 4 skipped; later stale under broad test-tree scope |
| E61 | `r4-mutable-protocol` | FAIL, 1 failed / 51 passed; post-refusal test assertion only |
| E62 | `r4-lint` | PASS, counts unknown; current after source/test correction |
| E63 | `r4-format` | PASS, counts unknown; stale after documentation edits |
| E64 | `r4-mutable-protocol` | PASS, 52 passed; current after final test edit |
| E65 | `r4-launch-protocol` | PASS, 5 passed; current after final source/test edit |
| E66 | `r4-parity` | PASS, 1 passed; current after paired manifest repair |

E51 reproduced both real source failures: queued no-budget effects were prepared without dispatch admission, and public agent registration attempted setup under an unrelated outer `al_command` Attempt. E59 later passed the actual public setup paths with 30 passed and four retained skips. E56 is a fixture mismatch: a migrated unit requested public product ownership `['.']`, which the existing guard correctly refused; the final test uses narrower `['app']` task ownership while still testing setup at `cwd='.'`. E61 failed only because its post-refusal assertion used a policy fixture without a directory; both safety refusals had already occurred. The final test now asserts unchanged setup outcomes and process handles, and E64 passes all 52 mutable/resource cases. No guard was weakened and no test assertion was removed. E59/E60 are preserved functional history but not current full-scope evidence after the later test-only change.

E55/E66 confirm the parity inventory marks `al_policy_retry.py` and `al_retry_control.py` shared only after the Engineer compared the actual paired file bytes. E62 lint, E64 mutable/resource, E65 launch and E66 parity remain current for their declared inputs. E63 formatter evidence predates documentation updates. This authored repair and its tests do not qualify arbitrary installer/native behavior, old process closure, or Linux/hosted runs. Skipped native/Codex smoke tests remain skips.

### R4 current return1 support verification

The post-repair current batch reused E62 lint, E64 mutable/resource regressions, E65 launch protocol and E66 parity because their declared inputs were unchanged. E67 also ran the three exact original E60 consumer modules alongside the setup suite, avoiding a duplicate `r4-consumer-regressions` invocation; the separately planned consumer003 output remains unused. Current source-tree results are:

| EID | Area | Result |
| --- | --- | --- |
| E67 | `r4-setup-protocol` | PASS, 30 passed / 4 skipped |
| E68 | `r4-control-regressions` | PASS, 24 passed |
| E69 | `r4-harness-contracts` | PASS, 21 passed |
| E70 | `r4-harness-snapshot` | PASS, 11 passed |
| E71 | `r4-installer-closure` | PASS, 18 passed |
| E72 | `r4-policy-regressions` | PASS, 56 passed |
| E73 | `r4-worker-regressions` | PASS, 19 passed |

E67 covers the new setup lifecycle cases and the same tooling, operation and continuity test files selected by the older consumer area. Its four skips remain unsupported cases. E68–E73 recheck the relevant control, contract, snapshot, installer, policy and worker boundaries against the repaired source. Each completed pytest entry retains `descendant_settlement_unproven`; functional results do not qualify whole-tree containment. The evidence manifest links their ledger logs and retained XML. A final repository-wide format check is still required after these documentation edits; the E63/E50 formatter outcomes are historical only. Formal AL approval, independent review and full-mission completion remain open.


### R4 independent-review repair support (E75–E89)

The independent diagnostic report and bounded Architect resolution, followed by the Engineer repair report and exact source manifest/diff, are archived in [review-repair-manifest-001.json](r4-evidence/review-repair-manifest-001.json). They are distinct records: the Architect resolution is not formal approval, the repair report is authored work, and fresh independent reinspection is still pending. The diagnostic found authenticated check reconciliation could invalidate its companion, operational issue updates could reject historical context after unrelated graph revision, standalone artifact output could accept criteria not grounded in the task/package, and the user guide used an incorrect CLI example. The repair preserves the original check entry and companion while recording versioned reconciliation, validates issue history separately from current authority, enforces actual task/package criteria before publication or idempotent return, and documents the actual command.

| EID | Area | Result / currency |
| --- | --- | --- |
| E75 | `r4-review-codex-repro` | FAIL, 4 failed / 16 deselected; causal pre-repair red |
| E76 | `r4-review-claude-repro` | FAIL, 4 failed / 16 deselected; causal pre-repair red |
| E77 | `r4-review-harness-repro` | FAIL, 1 failed; fixture scope miss, not source proof |
| E78 | `r4-review-harness-repro` | FAIL, 1 failed; corrected public-producer causal red |
| E79 | `r4-lint` | PASS; later stale after test/helper edits |
| E80 | `r4-format` | PASS, 651 formatted; stale after later docs edits |
| E81 | `r4-review-codex-repro` | FAIL, 1 failed / 9 passed / 16 deselected; helper fixture NameError |
| E82 | `r4-review-codex-repro` | PASS, 10 passed / 16 deselected on Engineer snapshot006 |
| E83 | `r4-review-claude-repro` | PASS, 10 passed / 16 deselected on Engineer snapshot006 |
| E84 | `r4-review-harness-repro` | PASS, 1 passed on Engineer snapshot006 |
| E85 | `r4-parity` | PASS, 1 passed on Engineer snapshot006 |
| E86 | `r4-control-regressions` | PASS, 24 passed on Engineer snapshot006 |
| E87 | `r4-installer-closure` | PASS, 18 passed on Engineer snapshot006 |
| E88 | `r4-lint` | PASS; counts unknown on Engineer snapshot006 |
| E89 | `r4-format` | PASS, 651 formatted on Engineer snapshot006; stale after this docs update |

E75–E89 logs remain in the protected ledger; the XMLs are retained under frozen review snapshot006 as listed in the evidence package. Every process record reports only its owned process group as settled; descendant settlement remains unproven. The Engineer-source focused results above do not replace the currently registered Codex and Claude broad AL suites, which are still pending at this documentation freeze. The format check is to run last after those results and any fixture-only correction. Existing unsupported native/provider/hosted CI cases, skips, older failures, and any formal independent reinspection remain separate.


E90 broad Codex regression run (3 failed / 136 passed, 476.68s) is archived with its exact log/XML in [the review evidence manifest](r4-evidence/review-repair-manifest-001.json). Two parameter variants of `test_pre_task_base_gate_is_real_but_candidate_and_label_spoof_are_refused` fail on the later public `integration task-register` path: current graph revision advances to 1 while the admitted implementation-plan companion retains captured revision 0. The test does not directly rewrite state. Read-only validation of the retained state reproduces `validate_admitted -> al_agent_exchange.consume_value -> C.validate` rejection for the stale context; the CLI log’s serialized pointer says `/payload`, while direct replay against the retained state reports `/context`. This is a production issue for Engineer review, not a fixture waiver.

The third E90 failure confirmed the known synthetic fixture problem in `test_retry_requires_crashed_check_reconciliation`: it changed an authenticated terminal entry to `RUNNING` directly. That fixture is now authored to use the real public evidence runner, wait for its persisted running entry, send SIGINT, reap the process, retain the original companion, assert retry refusal before reconciliation, and retry only after public reconciliation. This test-only correction remains unverified pending an admitted current run. Do not treat E90 as a post-correction pass.

## R5a Incident Intelligence dashboard

R5a adds Incident Intelligence to the existing execution dashboard, backed by
the persisted incident catalog and scoped `/api/reliability` reads/actions.
The UI keeps immutable observations and closed source provenance separate from
append-only operator hypotheses, evidence, controls, corrections, group
decisions and output verdicts. Search and evidence choices use actual incidents
within task or execution/epoch scope. Group suggestions describe related
symptoms, not proven common cause. Legacy projection and published-output
verdicts require the full execution scope; outputs are metadata-only and are
revalidated at submission. Local operator attribution is not named-user
authentication, approval or execution authority. The full accepted route,
cursor, replay and validation contract is in
[r5a-api-contract.md](r5a-api-contract.md); ordinary operator steps are in
[user-guide.md](user-guide.md#incident-intelligence-investigations-and-operator-decisions).

### R5a browser and support evidence

The following are actual protected ledger outcomes for session
`reliability-r5a-20261120`, run `c0b8fa62`. Failures are retained as history;
later fixes do not rewrite them. Each Chrome result runs the real persisted
backend fixture at desktop (1280×800) and narrow (390×844).

| EID | Area | Result |
| --- | --- | --- |
| E29 | `r5a-ui-syntax` | PASS, `node --check` |
| E30 | `r5a-incident-browser` | FAIL, 2 failed; fixture had no eligible present Knowledge source |
| E31 | `r5a-incident-browser` | FAIL, 2 failed; the approval-created spec was untracked and default `git ls-files --cached` inventory was empty |
| E32 | `r5a-incident-browser` | FAIL, 2 failed; output-verdict journey timed out |
| E33 | `r5a-incident-browser` | FAIL, 2 failed; service returned HTTP 409 for the submitted verdict |
| E34 | `r5a-incident-browser` | FAIL, 2 failed; decisive trace found the UI rationale default was not one of the accepted codes, so the submitted code was empty |
| E35 | `r5a-ui-syntax` | PASS, `node --check` |
| E36 | `r5a-incident-browser` | FAIL, 2 failed; page width expanded during the advanced-controls journey |
| E37 | `r5a-incident-browser` | FAIL, 2 failed; narrow layout still widened after the editor expanded |
| E38 | `r5a-incident-browser` | FAIL, 2 failed; evidence-select geometry exceeded the viewport |
| E39 | `r5a-incident-browser` | FAIL, 2 failed; initial CSS selector did not match the actual details element |
| E40 | `r5a-incident-browser` | FAIL, 2 failed; corrected selector alone did not resolve editor geometry |
| E41 | `r5a-incident-browser` | PASS, 2 passed |
| E42 | `r5a-incident-browser` | PASS, 2 passed; later screenshot inspection found stale group/output evidence choices after a scope/query restore |
| E43 | `r5a-incident-browser` | PASS, 2 passed; screenshot-time choice state was not yet captured after the final restore sequence |
| E44 | `r5a-incident-browser` | FAIL, 2 failed; combined primary-editor visibility assertion was stricter than the rendered interface |
| E45 | `r5a-incident-browser` | FAIL, 2 failed; `getClientRects()` did not establish that an optional field inside closed `<details>` was hidden |
| E46 | `r5a-incident-browser` | PASS, 2 passed in 20.55s |
| E47 | `r5a-ui-syntax` | PASS, counts unknown |
| E48 | `r5a-browser-regression` | PASS, 4 passed |
| E49 | `r5a-api` | PASS, 8 passed |
| E50 | `r5a-compatibility` | PASS, 123 passed |
| E51 | `r5a-lint` | FAIL, 69 lint findings in the owned browser test; preserved before repair |
| E52 | `r5a-incident-browser` | PASS, 2 passed in 23.10s after test-only formatting repair |
| E53 | `r5a-lint` | PASS, counts unknown |
| E54 | `r5a-foundation` | PASS, 37 passed |
| E55 | `r5a-parity` | PASS, 1 passed |
| E56 | `r5a-source-closure` | PASS, 53 passed |

E31's fixture uses the supported `RefreshRequest(included_paths=(actual_spec,))`
under the actual task operator scope, then selects the returned present
snapshot/version; it does not insert Knowledge rows or relax eligibility.
E34 retained the service's validation and corrected the UI to a supported
rationale. E36–E45 preserved layout, visible-picker, primary-editor and
optional-disclosure assertions while tracing each browser failure. E46 verifies
the final screenshot-time evidence options are visible and populated, primary
controls remain visible/focusable with optional controls closed, optional
fields cannot receive focus while closed, the inline hypothesis checkbox
alignment, successful persisted output verdicts and the delayed-scope fence.
The persisted functional records show `finalOverflow: false` and a 390px
visual/document/body width for the narrow case. The desktop and narrow E46/E52
screenshots, existing-dashboard regression screenshots from E48, JSON behavior
records, JUnit XML, exact E29–E56 ledger logs, captured planning/engineering
reports and parent visual observations are hashed in
[r5a-evidence/manifest.json](r5a-evidence/manifest.json). Parent visual
observations are explicitly not formal independent review or approval.

The ledger records `descendant_settlement_unproven` on executed test runs;
owned process-group exit does not prove whole-tree settlement. E46 establishes
this dashboard journey at the checked browser sizes, not hosted Chrome/CI,
general native/provider qualification, complete incident-source coverage,
formal AL approval or completion of R5b and later mission phases. Other
E47–E50 and E52–E56 are current support results for the UI source/test
surface, incident API, migration, dashboard-browser compatibility, catalog
foundation, platform parity and trusted source-closure suites.
E51 retains the initial lint failure; the unused import and overlong embedded
browser-test lines were corrected without changing assertions, after which the
same browser journey passed at E52 and `ruff check .` passed at E53. E52's fresh
screenshots and functional records are also archived. E55 and E56 are new
current parity and source-closure runs; the older E20/E21 outcomes remain
historical. The Codex and Claude producer areas E22/E23 remain stale under the
authoritative handoff and were not rerun because the later upstream integration
baseline is not yet reconciled. This scoped pass is not a mission-completion
claim.

The parent inspected all ten E46 screenshots and reported no remaining
incident-section visual/alignment/overflow issue. Its observation records the
actual image hashes and dimensions in
[r5a-evidence/manifest.json](r5a-evidence/manifest.json); the existing
workspace tab rail is horizontally scrollable, while page/document width
stays within the viewport. This is a visual observation, not a formal review
or approval.


### R4 plan, escalation-history and exact-delivery repair

The successor repair report and the factual recovery record are archived, with source hashes/delta, contribution attribution and E116–E128 logs/XML, in [delivery-buffer-repair-manifest-001.json](r4-evidence/delivery-buffer-repair-manifest-001.json). The 9,275-byte Engineer escalation report is explicitly partial; the 3,935-byte supervisor recovery report is not presented as an Engineer final report. The ten-path authenticated contribution is captured by the exact manifest and delta.

The plan consumer now retains `planning_authorship.contract_observation` at report publication. `consume_plan` validates that exact snapshot and permits the plan’s captured graph revision only when it is an integer no later than the current graph revision; current specification lineage, report digest and other context remain checked. This repairs the public task-registration failure described in E90. **Correction to the preceding E90 Builder note:** the two plan-case root causes are graph-context mismatches (`/context`, captured graph 0/current graph 1). The earlier attribution of `/payload` to those plan failures was incorrect. The literal `/payload` refusal in E90 belongs to the separate synthetic crashed-check fixture, which had overwritten an authenticated evidence entry without its companion. The archived E90 report remains unchanged; this paragraph is the correction.

Escalation registration stores an `escalation-observation-v1` snapshot containing the original blocker row and companion. Decision and revalidation transitions append immutable observations and referenced artifacts. Consumption checks the bounded sequence (original, decision, optional revalidation), exact durable acknowledgement/revalidation, and current row/companion. Existing task/authority guards remain. The trusted bridge’s decision-only `delivery_receipt` has exactly `schema_version: 1` and a lowercase 64-character `sha256`; it is produced from the exact Registry delivery bytes after fixed-inbox equality and Registry validation. AL performs one bounded no-follow read, checks the raw digest before parsing, compares the parsed object and snapshots that same buffer. Nondecision bridge replies are unchanged. This evidence does not authenticate a named human or establish general filesystem, native, provider or hosted execution qualification.

| EID | Area | Result |
| --- | --- | --- |
| E116 | `r4-escalation-codex-repro` | FAIL, 1 failed / 12 passed; real Registry-backed replacement red |
| E117 | `r4-lint` | FAIL, counts unknown; fixture re-export lint issue |
| E118 | `r4-escalation-codex-consumers` | FAIL, 3 failed / 17 passed; strict receipt digest grammar safely refused prefixed digest |
| E119 | `r4-escalation-codex-consumers` | PASS, 20 passed; Codex decision module |
| E120 | `r4-escalation-claude-consumers` | PASS, 20 passed; Claude decision module |
| E121 | `r4-delivery-authority-consumers` | FAIL, 1 failed / 9 passed; fixture tuple slot mismatch |
| E122 | `r4-delivery-authority-consumers` | FAIL, 1 failed / 9 passed; fixture omitted actual source replay; authority refusal preserved |
| E123 | `r4-delivery-authority-consumers` | PASS, 10 passed with supported replay |
| E124 | `r4-parity` | PASS, 1 passed |
| E125 | `r4-control-regressions` | PASS, 24 passed |
| E126 | `r4-installer-closure` | PASS, 18 passed |
| E127 | `r4-lint` | PASS, counts unknown |
| E128 | `r4-format` | PASS, 654 files formatted |

E119/E120 are real full decision-module functional passes; later root-only Harness fixture corrections did not change production or platform test bytes, but overall source/test coverage currency must be read from the ledger. E123 establishes the corrected supported Harness source replay. All check history remains distinct from the new broad Codex/Claude suites below. Process records retain `descendant_settlement_unproven`.

Recovery status is also preserved: the third rework request returned exit 10 with RR1 retained at `REWORK_REASSESSMENT`; publication based on an Architect recommendation was refused with `PROOF_ADMISSION_PENDING`, so no accepted receipt or new credit was created. The first ordinary writer admission after the refused RR1 publication used one actual `implementation-continuity-v1` record, with RR1 still pending, usage 2 of 2, and automatic authorizations 0. It authorized defined implementation work only; it did not clear the gate or amount to acceptance.


The earlier plan repair evidence is also archived: E93/E94 causal reds, the exact Engineer plan report, authored source hashes and delta. The earlier escalation-authored hash manifest and E106/E107/E109/E110 causal histories are retained separately from the later exact-buffer successor report. See [delivery-buffer-repair-manifest-001.json](r4-evidence/delivery-buffer-repair-manifest-001.json). These additions preserve original logs/outcomes and do not alter the frozen report files.


### R4 final registered broad AL suites

| EID | Area | Result |
| --- | --- | --- |
| E129 | `r4-review-al-codex-broad` | PASS, 148 passed |
| E130 | `r4-review-al-claude-broad` | PASS, 127 passed |

Both results use the registered 10-selector Codex and 8-selector Claude platform recipes with distinct fresh `002` basetemp/XML outputs. Codex includes the real interrupted-check reconciliation fixture and execution-receipt/runtime-batch5 cases; Claude has only files present in its platform tree. Exact ledger logs and XML are archived in [delivery-buffer-repair-manifest-001.json](r4-evidence/delivery-buffer-repair-manifest-001.json). Each run retains `descendant_settlement_unproven`; the functional passes do not establish whole-tree settlement or hosted/native qualification. These two broad results are separate from E119/E120 decision-module passes and the retained causal failures E93/E94/E106/E107/E109/E110.
