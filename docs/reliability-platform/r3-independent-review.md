# R3 independent diagnostic assessment and repair provenance

This record preserves the original scoped independent assessment, its authored repair evidence, and a subsequent independent diagnostic reinspection. The latter found all three original findings fixed in frozen candidate003 within the inspected scope. Neither report is a formal AL approval or exhaustive review of all source.

## Assessment provenance

- Exact assessment source: `/private/tmp/al-r3-fresh-review-001.md`.
- Assessed frozen candidate: `/private/tmp/al-r3-frozen-review-002`.
- Assessment binding: accepted package SHA-256 `fa86634679d2ec14d096263c98a8beb69d184865819cee76f68de14e8db484a3`; source manifest SHA-256 `cc58f34948214cc40bf21a2191d0d0587afbbee1253b8745b370d5458a6febdb`; selected diff SHA-256 `12d71dcafb579e95160a75ced38854fbc8f11c27ad27567fc6001c4f8312eb25`.
- Assessment type: targeted source inspection only. The three findings below were counterexamples derived from inspected code, not executed reproductions. The assessor explicitly stated this was not exhaustive approval of every changed line. The captured report does not identify the assessor or native thread ID; none is inferred here.
- The exact source line references below refer to the frozen candidate, not necessarily the current checkout line numbers.

## Findings as assessed

### R3-FRESH-001 — BLOCKING — Referenced parent limits change when a shared pool is superseded

Criteria: R3-02, R3-06 and R3-I2. The assessed sequence installed parent P-v1 with a one-mutation ceiling, installed an applicable child referencing P-v1, consumed one unit, then installed P-v2 raising the same pool to ten without changing the child reference. The child still selected immutable P-v1, but admission used the overwritten shared maximum. The report cited `composition.py:50` for recursive selection, `composition.py:79` for retaining only the pool ID, `configuration.py:152` for overwriting the maximum, and `admission.py:218` for checking the overwritten value. It required retaining shared counters while applying the restrictive maximum from all selected documents, with both the unchanged child and a v2-only consumer covered.

### R3-FRESH-002 — BLOCKING — Patch commit proceeds after its deadline expires

Criteria: R3-08 and R3-13. The assessed sequence let a governed patch reach its deadline during target metadata lookup or temporary-file write/fsync, then observed unconditional unlink/replacement. The report cited `execution_files.py:254` for the pre-loop check and `execution_files.py:278` for the later replacement, and noted deletion had no intervening check. It required checking before later destructive steps after potentially blocking work, while preserving cleanup and consumed charges. It did not assert that an already running syscall could be interrupted.

### R3-FRESH-003 — BLOCKING — Repository-audit reads bypass resource claims

Criteria: R3-03, R3-16, and claims-before-read-through-use. The assessed sequence retained an unresolved source write claim, then requested `read_file` for `harness:repository-audit`; the audit traversed manifests/source without a derived claim. The report cited `resources.py:52` for excluding harness resources, `resource_bindings.py:25` for producing no claims, `claims.py:42` for conflict checking only derived claims, `execution_operations.py:622` for invoking the live audit, and `repository_audit.py:68`/`:173` for reads. It allowed either conservative pre-read claims or refusal of the unsupported resource under affected enrollment.

The assessor also viewed four historical E98 screenshots and found no clear visual misalignment; E98 was explicitly historical. The assessment stated that E121/E122 did not exercise these three counterexamples, and that formal gates, full check coverage, native qualification and production readiness were not established.

## Reproduction and repair history

The actual reproduction handback is `/private/tmp/al-r3-review-repro-report.md`; the production repair handback is `/private/tmp/al-r3-review-repair-report.md`. The protected ledger session is `reliability-r3-20261119`, run `a5c3cd80`.

- E124 is the causal reproduction: **FAIL, 12 failed / 35 passed**. It exercised one immutable-parent widening case, eight public patch mutation-boundary cases, and three live repository-audit claim-bypass cases. Its exact XML is archived as `r3-evidence/E124-red.xml`.
- E127 is an intermediate repair pass: **PASS, 53 passed**. It predates the adjacent source-route classifier fixes and is historical, not final-source proof. Its exact XML is archived as `r3-evidence/E127-intermediate.xml`.
- E132 is a later causal classifier red: **FAIL, 16 failed / 67 passed**. Twelve real byte-read paths bypassed unresolved claims across unknown `harness:` fallthrough/diagnostic routes, and four matching file-scope cases were incorrectly rejected. E132 is a distinct red and is not concealed by E127. Its exact XML is archived as `r3-evidence/E132-classifier-red.xml`.
- E134 is the final matching reproduction-area result: **PASS, 83 passed**. E135 core: **PASS, 124 passed**. E136 file regressions: **PASS, 27 passed / 4 skipped**. The exact XML files are archived with their source paths and SHA-256 hashes in the evidence manifest. E133 lint and E137 format passed on final authored source. These authored checks do not themselves constitute independent reinspection or formal AL approval.

The current repair retains minimum ceilings across each selected immutable policy document while leaving shared accounting rows/counters intact. Patch logic checks deadlines before later filesystem mutations following blocking work; cleanup remains unconditional and an already-completed target effect is not claimed rolled back. Coordinated live repository audit derives a conservative repository-wide dependency claim and refuses with the fixed unsupported-enforcement response before source/Git traversal because that route has no qualified coordinated execution boundary.

Adjacent classification follows the current named handlers: unknown `harness:` names that actually fall through to ordinary file reads are path-validated and assigned file resources; descriptor-backed diagnostic `source`, `log` and `config` paths derive their file claims before bytes and retain claims through response construction. They are not cleanup exemptions and do not acquire strict time qualification. Tooling/capabilities preparation uses filesystem presence checks; it does not read manifest bytes. This is not an exhaustive qualification of opaque helper routes.

## Public shared-pool label

The policy UI displays the API's current shared pool maximum as **Shared pool limit**. It explains that selected and inherited immutable ceilings can constrain a particular operation without resetting shared counters. The API shape and value are unchanged; no effective role aggregate is synthesized. E138 JavaScript syntax passed after this UI edit; E139 actual Chrome desktop/narrow interaction and visual run passed 2 tests. The retained functional records report independent detail/list and capability paging, all 105 capabilities observed across pages, focus/disclosure retention, 106 decision history items, visible controls within the section, no horizontal document overflow, and no unsafe test marker. The actual screenshots and JSON are archived in `r3-evidence/` with SHA-256 provenance. Visual inspection found no clipping, horizontal overflow, wrapping, alignment or control issue at 1280x800 and 390x844.

## Remaining limits

The read/patch deadline mechanism is cooperative: it cannot preempt an OS syscall already in progress or provide a hard scheduling/latency bound. General process/native/provider deadlines, arbitrary commands, Linux/container execution and hosted behavior remain unqualified. The three findings below have since received the diagnostic reinspection recorded in the next section. Full mandatory verification/coverage, formal AL review/approval and the R4+ mission remain separate obligations.


## Subsequent independent diagnostic reinspection

- Exact retained report: [R3-reinspection-001.md](r3-evidence/R3-reinspection-001.md), copied byte-for-byte from `/private/tmp/al-r3-review-resolutions-001.md` (SHA-256 `56d22dc8e4822c9c57365b8bf7bdbe74fecff18e93f7fe3322a786e16cfde925`).
- Inspected frozen source candidate: `/private/tmp/al-r3-frozen-review-003`; source-manifest SHA-256 `7028a0dd781faed190dfdd13a6040a7d004eda77a9ff90465a48b39f2149049e`.
- The diagnostic reinspection reports R3-FRESH-001, R3-FRESH-002 and R3-FRESH-003 fixed and found no new concrete counterexample in its inspected repair scope. It also checked the classifier's adjacent ordinary-file and descriptor-diagnostic routes, API shared-pool semantics, documentation, archived evidence and E139 UI. It is a scoped reinspection, not formal AL approval or exhaustive source review.
- E140 · `r3-review-repro` is **PASS, 83 passed, zero failures/errors/skips** and was marked VALID at the frozen003 review. The exact XML is archived as [E140-r3-review-resolution.xml](r3-evidence/E140-r3-review-resolution.xml), with original source and hashes in [manifest.json](r3-evidence/manifest.json). This is historical R3 evidence against frozen003, not a current full-source R4 proof.
- E124 and E132 remain genuine retained red histories; E127 remains an intermediate pass, not final-source proof. E134/E135/E136/E138/E139 retain their separate authored verification scopes; E136 still includes four skips. No failure or skip is overwritten by E140.
- The diagnostic reinspection independently viewed all four E139 desktop/narrow screenshots and reported no clipping or horizontal overflow, with visible focus in capability pagination. Its visual assessment and E139 test are separate from proof of formal review or production readiness.

The original R3 findings and their exact source-derived triggers remain above for auditability. Their current diagnostic resolution does not upgrade old pins, qualify strict native/process/provider deadlines, establish Linux/container or hosted behavior, or complete the broader mission.
