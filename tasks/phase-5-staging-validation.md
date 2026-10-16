# Phase 5 — Staging Validation & Production-Ready V1

**Goal:** validate the real deployed result against the approved acceptance criteria in a browser (with separate approval), harden the whole system, and ship V1 with documentation.
**Estimate:** 6–7 working days · **Roadmap:** [docs/ROADMAP.md](../docs/ROADMAP.md#phase-5--staging-validation--production-ready-v1) · **Spec refs:** §3 (steps 10–11), §7, §11, §12, §14
**Depends on:** Phase 4

## Tasks

### P5-01 — Browser automation capability check
- [ ] Verify which tool can drive staging (Claude in Chrome, Codex browser automation or another compatible tool)
- [ ] Record the decision and its limits. If none works, report that clearly and don't fake it

### P5-02 — Browser-testable AC selection
- [ ] From the approved acceptance criteria, find the ones that can be tested in a browser
- [ ] Generate a browser validation plan

### P5-03 — Browser test approval gate
- [ ] Browser tests need their **own** explicit approval, separate from spec approval (AC 24)

### P5-04 — Browser Validation Controller
- [ ] Run the approved plan against the **real** staging app
- [ ] Record per criterion: pass/fail, evidence (screenshots etc.), errors, blockers (AC 25)
- [ ] Failures go to investigation. A failure never authorizes a new architecture decision

### P5-05 — Reporting Service (completion summary)
- [ ] What was implemented, completed tasks, test results, MR link/status, CI/CD, staging, browser results, remaining blockers, deviations that need approval (AC 26)
- [ ] Concise output. Detailed verification stays with AL

### P5-06 — Recovery & idempotency hardening
- [ ] Interrupted sessions restore the latest consistent state (§12)
- [ ] Completed external actions (MR creation, AL launch, browser runs) are never repeated
- [ ] Retry policies for external integrations. "Integration unavailable" is reported, never faked

### P5-07 — Security verification
- [ ] Tests for permissions, command classification, read-only DB enforcement, secret redaction, no secrets in Git
- [ ] Tests that auto-execution modes cannot bypass the approval gates

### P5-08 — V1 acceptance criteria sweep
- [ ] Walk through all 30 acceptance criteria in [SPEC §14](../docs/SPEC.md) and link each one to a test or demo

### P5-09 — Documentation
- [ ] Installation, configuration (GitLab, AL, browser tool, DB read-only creds), usage guide, troubleshooting

### P5-10 — Phase 5 / V1 acceptance
- [ ] Run a real feature end to end → staging test → a final report you can rely on with test results, MR and pipeline
- [ ] No regressions in Phases 1–4. Final demo

## Phase exit checklist
- [ ] All tasks done, all tests green
- [ ] All 30 V1 acceptance criteria verified
- [ ] Demo recorded
- [ ] Docs complete
