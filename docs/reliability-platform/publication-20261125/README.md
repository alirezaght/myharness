# Rebased publication checkpoint archive

This directory preserves source-bound planning, review, and Engineer evidence for the rebased 2026-11-25 candidate. `provenance.json` records the original `/private/tmp` path and SHA-256 of every archived report/manifest, and verifies the copied bytes. The original task package and prepared R5b–R11 packages remain immutable in their existing locations.

## Candidate and publication status

The candidate rebases preserved checkpoint `ded9054310d1f8ffe90736b192fcfaec557be3c9` onto main `fef2cd1ec5650c7fba5cf9f056302d105f5c64a5`. The current clean core checkpoint is `91d58709207fbb3b10c6546dbe6b73b6045bea65` on `codex/reliability-publication-rebase-20261125`. Builder support changes are being added separately. The candidate is not pushed; independent review and a distinct final verification/publication run against current main remain pending. Old run `c82f62fb` retains an obsolete stored merge base and is not final publication proof.

## Packaging evidence

The built wheel inspected under E41 contained `harness/migration_schema_manifest.json`, all 25 canonical migration SQL files, and `al/installer/__init__.py`. No `pyproject.toml` package inclusion change was needed. E43 passed both tests in `tests/test_planning_session_distribution.py`:

- The existing wheel/sdist skill and session-project probe now uses `python -I -S`, checks `harness` and `al.installer` origins, and verifies every migration and the schema manifest are in both artifacts, while retaining its no-default-database assertion.
- A separate installed-wheel probe runs from a temporary working directory outside the checkout with `-I -S` and only the `--target` install on `sys.path`. It imports `harness` and `al.installer`, reads the packaged manifest and all SQL resources, checks each SQL SHA-256, calls `harness.db.connect` on an owned disposable database, and observes the complete canonical 1–25 ledger.

E41 was the original one-test distribution PASS before this expansion. E42 recorded the first expanded run as FAIL (1 failed, 1 passed): the older probe checked `installer_origin` but omitted it from its JSON output. E43 fixed that probe-result omission and passed (2 passed). The E42 external JUnit XML path was reused by E43 and may have been overwritten; the immutable AL-managed E42 log remains the historical record.

## Timing acceptance and limitations

E38 remains an overall FAIL with 111 functional passes and 34 timing-budget violations. The user explicitly accepted this result and requested no more timing optimization. The existing 5-second per-test and 60-second per-file limits are unchanged; the timing failure is not relabeled or waived. Earlier failures and recovery history remain immutable. R4 recorded two returns used of two total and zero automatic authorizations/grants; the Architect's RR1 allowance recommendation was not accepted, and the runtime recorded `PROOF_ADMISSION_PENDING`. The earlier Builder report and handoff record misstated this as exhausted automatic grants; they are preserved unchanged, with the correction in the append-only [Builder correction record](builder-handoff-record-002-20261125.json) and corrected report at `/private/tmp/reliability-publication-builder-report-20261125-corrected.md`.

## Implemented migration and authority limits

The integration preserves upstream migrations `0019_investigation_sources.sql` and `0020_planning_sessions.sql`; checkpoint reliability migrations are canonical `0021`–`0025`. Migration recognition checks exact filenames and compatible managed schema, and known checkpoint histories relocate atomically while preserving timestamps and data. Current canonical databases open without a write lock. Unknown or incompatible histories refuse without manual ledger edits. The schema manifest is checked in and binds exact SQL hashes and managed objects.

Historical AL planning rebind requires the exact associated historical analysis observation; unbound or ambiguous historical inputs refuse and direct the operator to obtain fresh current analysis. Sealed retry and exact writer-finish qualification applies only to the source-audited exact invocation and its bound helpers. Service responses, cache hits, process-group disappearance, and PID absence do not establish universal descendant settlement or publication authority.

## Chrome evidence and visual review

E44 `publication-browser-focused` passed 11 tests. It covered the incident intelligence editor and persisted history, real-process timeline, policy decisions and capabilities, attached-session history, and investigation source review/adoption. The retained run output is `/private/tmp/publication-browser-focused-001/`; investigation interview/theme screenshots are in `/private/tmp/hp25b/harness-interview-recovery-artifacts/outputs/`. The registered E44 command used the prepared Chrome wrapper, `TMPDIR=/private/tmp/hp25b`, and a unique pytest basetemp. Final support format/lint runs E47 and E48 passed; Ruff reported 786 files already formatted and all lint checks passed.

I visually inspected incident desktop (1280×800) and narrow (390×844), policy desktop/narrow and capabilities, timeline desktop/narrow, investigation interview desktop/narrow, dark narrow diagram, and desktop acceptance/review captures. Root separately inspected seven incident and related screenshots. Its preliminary visual note is preserved as `root-visual-observation.json`, copied from `/private/tmp/reliability-publication-root-visual-observation-20261125.json` with matching SHA-256 `14ed56b73baf027ce1249a77200bd066bab84b0b76a627a6c3a62ef0f5a3a6fd`. The captures show no major alignment defect or page-level horizontal overflow; narrow views wrap content and scroll vertically, and navigation tabs continue horizontally. One nonblocking usability limitation is visible in the incident editor: native Supporting and Contradicting evidence multi-select labels are clipped at both viewport widths. The full incident ID is retained as the option value and shown in saved hypothesis detail; selecting an incident opens its immutable fact, source provenance, and investigation history (`harness/dashboard/static/reliability.js`, `iiAddFactFields`, `iiRenderSource`, `iiRenderDocument`). This provides a separate route to inspect the underlying evidence. No UI change was made for this bounded review.

Representative screenshots:

- Incident desktop/narrow: `/private/tmp/publication-browser-focused-001/test_persisted_incident_intell0/screenshots-desktop/incident-intelligence-editor-1280x800.png` and `/private/tmp/publication-browser-focused-001/test_persisted_incident_intell1/screenshots-narrow/incident-intelligence-editor-390x844.png`.
- Policy desktop/narrow: `/private/tmp/publication-browser-focused-001/test_policy_flow_in_real_chrom0/policy-shots/policy-1280x800.png` and `/private/tmp/publication-browser-focused-001/test_policy_flow_in_real_chrom1/policy-shots/policy-390x844.png`.
- Timeline desktop/narrow: `/private/tmp/publication-browser-focused-001/test_real_process_timeline_in_0/timeline-shots/timeline-1280x800.png` and `/private/tmp/publication-browser-focused-001/test_real_process_timeline_in_1/timeline-shots/timeline-390x844.png`.
- Investigation interview desktop/narrow: `/private/tmp/hp25b/harness-interview-recovery-artifacts/outputs/investigation-interview-1280-light-80c0676149.png` and `/private/tmp/hp25b/harness-interview-recovery-artifacts/outputs/investigation-interview-390-light-7464738bd0.png`.

The browser pass and screenshot inspection establish these selected journeys only. They do not establish full browser-suite coverage or independent review. Hosted Linux containment, opt-in provider/native execution, universal descendant settlement, and other unavailable environment evidence remain explicit limitations.

R5b–R11 remain subsequent approved work requiring revalidation against the merged consumers. Use `0026` only as the provisional next migration number. Do not add a blanket Knowledge acceptance gate. This checkpoint is not a claim of full mission completion or production readiness.
