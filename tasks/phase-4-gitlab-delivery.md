# Phase 4 — GitLab & Delivery Automation

**Goal:** an AL run ends in a real merge request with a pipeline you can track. Technical failures route back to AL, while design changes route to the developer. Read-only command safety is enforced.
**Estimate:** 3–5 working days · **Roadmap:** [docs/ROADMAP.md](../docs/ROADMAP.md#phase-4--gitlab--delivery-automation) · **Spec refs:** §3 (step 9), §4, §8.8, §10, §11
**Depends on:** Phase 3

## Tasks

### P4-01 — GitLab API client
- [ ] Official GitLab API with the developer's credentials
- [ ] Read repository and branch info, MRs, pipelines, jobs, environments/deployments
- [ ] Credentials come from a secure store or env, and never land in specs, Git or dashboard logs (§10)

### P4-02 — MR creation through AL
- [ ] AL creates the branch + MR as part of the authorized run
- [ ] The harness records the MR reference against the run and spec version
- [ ] **No automatic merge** into protected branches. Repository protections are respected

### P4-03 — Pipeline & CI job monitoring
- [ ] Track MR status, pipeline status, per-job results and test results (AC 22)
- [ ] Emit delivery events into the activity feed

### P4-04 — Failure routing
- [ ] Classify each failure as **technical** (send to AL for correction) or **design change** (send to the developer for approval)
- [ ] Record retries. Avoid infinite fix loops (bounded retries, then Blocked)

### P4-05 — Staging deployment detection
- [ ] Detect when the change is deployed to staging (AC 23)
- [ ] Expose a "staging ready" signal for Phase 5

### P4-06 — Delivery panel in the dashboard
- [ ] MR ref + status, pipeline status, CI job results, staging status, browser validation status (placeholder), remaining blockers (§8.8)

### P4-07 — Command Safety Controller
- [ ] Classify commands as safe read-only (auto-run), potentially mutating (needs approval) or unknown/high-risk (needs approval or a verified read-only alternative) (§4)
- [ ] Classify by actual side effects, not by command name
- [ ] Claude Code / Codex auto-execution modes cannot bypass the harness rules
- [ ] Audit record for every significant operation (§11)

### P4-08 — Read-only diagnostics
- [ ] Logs, container/service status, config, Git history, CI results, monitoring reads
- [ ] Read-only DB access: read-only credentials, restricted permissions, query validation, timeouts and resource limits. `SELECT` alone is **not** proof of safety (§4)
- [ ] Redact sensitive output

### P4-09 — Phase 4 acceptance
- [ ] Scenario: break a pipeline on purpose with a fixable error → AL finds and fixes it → final status (success or Blocked) is reported correctly
- [ ] A mutating command before approval is refused or sent for approval
- [ ] No regressions in Phases 1–3. Docs and demo

## Phase exit checklist
- [ ] All tasks done, all tests green
- [ ] Demo recorded
- [ ] Docs updated
