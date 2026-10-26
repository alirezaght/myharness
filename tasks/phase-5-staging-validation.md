# Phase 5 — Staging Validation & Production-Ready V1

**Goal:** validate the real deployed result against the approved acceptance criteria in a browser (with separate approval), harden the whole system, and ship V1 with documentation.
**Estimate:** 6–7 working days · **Roadmap:** [docs/ROADMAP.md](../docs/ROADMAP.md#phase-5--staging-validation--production-ready-v1) · **Spec refs:** §3 (steps 10–11), §7, §11, §12, §14
**Depends on:** Phase 4

## Tasks

**Implementation status:** local Phase 5 plan/approval/controller, reporting,
dashboard, recovery/security coverage, acceptance map, fixture demonstration and
documentation are implemented. Focused verification is recorded in the AL
ledger. The release checklist below remains open where safe real-browser,
real-staging or complete-regression evidence is required. No live staging target
was requested for this session; live release acceptance is outside this run.

### P5-01 — Browser automation capability check
- [ ] Verify a usable production browser integration for staging (Chrome CDP is implemented; installed, cached and newly downloaded Chrome binaries failed signature checks in this environment)
- [x] Record the selected integration and its limits. A safely usable capability probe is still needed before live execution; no capability success is claimed

### P5-02 — Browser-testable AC selection
- [x] Derive the exact criterion inventory from the approved specification and preserve selected, excluded and unresolved states
- [x] Generate a reviewable browser validation plan with explicit mappings and target/deployment binding

### P5-03 — Browser test approval gate
- [x] Browser tests require their **own** explicit approval, separate from spec approval (AC 24)

### P5-04 — Browser Validation Controller
- [ ] Run the approved plan against the **real** staging app (not performed in this session)
- [x] Persist criterion-level status, evidence metadata, errors and blockers; only fixture/controller evidence is currently verified (AC 25)
- [x] Route failures to investigation without authorizing an architecture decision

### P5-05 — Reporting Service (completion summary)
- [x] Report implementation, completed tasks, separate AL/CI test sources, MR, CI/CD, staging, browser status, blockers and deviations for approval (AC 26)
- [x] Keep the completion summary concise and detailed verification in AL artifacts

### P5-06 — Recovery & idempotency hardening
- [x] Interrupted sessions restore the latest consistent state from durable receipts (§12)
- [x] Completed and uncertain external actions (MR creation, AL launch, browser runs) are never implicitly repeated
- [x] Bound read retries for provider integrations and report unavailable integrations without simulated success

### P5-07 — Security verification
- [x] Exercise permissions, command classification, read-only DB protections, redaction, private artifacts and secret handling
- [x] Exercise that automatic modes cannot bypass browser and implementation approval gates

### P5-08 — V1 acceptance criteria sweep
- [x] Map all 30 acceptance criteria in [SPEC §14](../docs/SPEC.md) to a concrete test/demo reference and keep verification status explicit

### P5-09 — Documentation
- [x] Document installation, GitLab/GitHub, AL/browser configuration, read-only DB guidance, usage and troubleshooting

### P5-10 — Phase 5 / V1 acceptance
- [ ] Run a real feature end to end → staging test → a final report with current results, MR and pipeline (release evidence outside this session)
- [ ] Complete Phases 1–4 regression with all checks green; E33 remains failed on recorded environment/baseline issues and migration/CLI assertions were corrected afterward
- [x] Add and verify a deterministic isolated Phase 5 fixture demo; it does not establish live acceptance

## Phase exit checklist
- [ ] All tasks done, all tests green
- [ ] All 30 V1 acceptance criteria verified
- [ ] Real browser/staging demo recorded (local fixture demo exists but is not release evidence)
- [x] Implementation and operation docs complete
