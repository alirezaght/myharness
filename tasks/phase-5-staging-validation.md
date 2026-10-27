# Phase 5 — Staging Validation & Production-Ready V1

**Goal:** validate the real deployed result against the approved acceptance criteria in a browser (with separate approval), harden the whole system, and ship V1 with documentation.
**Estimate:** 6–7 working days · **Roadmap:** [docs/ROADMAP.md](../docs/ROADMAP.md#phase-5--staging-validation--production-ready-v1) · **Spec refs:** §3 (steps 10–11), §7, §11, §12, §14
**Depends on:** Phase 4

## Tasks

**Implementation status:** local Phase 5 plan/approval/controller, reporting,
dashboard, recovery/security coverage, acceptance map, fixture demonstration and
documentation are implemented. This developer-authorized self-maintenance run
uses preserved direct test logs; earlier AL ledger receipts retain their original
outcomes. The release checklist stays open where external staging/provider
acceptance is unverified. The developer selected a simple static petshop as the acceptance target. Real
loopback Chrome desktop/mobile acceptance passed on 2026-10-27; this does not
claim an external provider pipeline or production deployment.

### P5-01 — Browser automation capability check
- [x] Verify a usable real browser integration: existing native macOS Chrome Headless Shell passed capability/interception and petshop desktop/mobile checks on 2026-10-27
- [x] Record the selected integration and limits: isolated anonymous Chrome CDP; the normal Chrome profiles inject extension workers and remain refused by the target guard. All 18 browser-marked tests and the earlier selected 11-check browser/driver run passed with Headless Shell

### P5-02 — Browser-testable AC selection
- [x] Derive the exact criterion inventory from the approved specification and preserve selected, excluded and unresolved states
- [x] Generate a reviewable browser validation plan with explicit mappings and target/deployment binding

### P5-03 — Browser test approval gate
- [x] Browser tests require their **own** explicit approval, separate from spec approval (AC 24)

### P5-04 — Browser Validation Controller
- [x] Run real Chrome against the developer-approved static petshop on loopback, covering catalog, add/remove/clear, empty state, keyboard focus, desktop/mobile layout and screenshots
- [ ] Run the provider-bound controller against an external staging deployment (not requested for the replacement petshop example; no external staging success claimed)
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
- [x] Run the supported Phases 1–4 regression: 757 non-browser tests passed, 17 native/environment tests skipped, and all 18 native Chrome browser tests passed. Historical E33 failures remain preserved; direct current results are recorded separately. Signed-in provider acceptance is unverified
- [x] Add and verify a deterministic isolated Phase 5 fixture demo; it does not establish live acceptance

## Phase exit checklist
- [ ] All tasks done, all tests green
- [ ] All 30 V1 acceptance criteria verified
- [x] Real browser petshop example recorded with desktop/mobile screenshots and all 11 selected checks passing; external deployment acceptance remains separate
- [x] Implementation and operation docs complete

## 2026-10-27 real browser evidence

`tests/test_petshop_browser.py`, `tests/test_dashboard_browser.py`,
`tests/test_dashboard_execution_browser.py` and `tests/test_browser_driver.py`
passed unchanged in the selected native Chrome Headless Shell run: **11 passed**.
The complete browser-marked suite subsequently passed **all 18 tests**. Desktop/mobile
screenshots, cart totals and keyboard focus were visually inspected. The final
local log is `/private/tmp/phase5-final-native-browser.log`; artifacts are under
`/private/tmp/phase5-final-native-browser/`. This is real application/browser
evidence, distinct from the synthetic provider/controller demonstration. Full
regression and publication evidence are recorded separately at release closeout.
The current non-browser run passed **757 tests**, with **17 skips**.

Visual review found one existing cosmetic issue: reciprocal component-diagram
arrows cross their labels. The petshop and dashboard interaction/viewport checks
passed. The complete browser log is `/private/tmp/phase5-complete-native-browser.log`;
original screenshots and the derived detail are retained with the visual review
in `/private/tmp/phase5-visual-review.md`.
