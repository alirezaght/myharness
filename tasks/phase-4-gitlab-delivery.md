# Phase 4 — GitHub, GitLab & Delivery Automation

**Goal:** an AL run ends in a real pull/merge request with a pipeline you can track. Technical failures route back to AL, while design changes route to the developer. Read-only command safety is enforced.
**Estimate:** 3–5 working days · **Roadmap:** [docs/ROADMAP.md](../docs/ROADMAP.md#phase-4--gitlab--delivery-automation) · **Spec refs:** §3 (step 9), §4, §8.8, §10, §11
**Depends on:** Phase 3

## Tasks

### P4-01 — GitHub and GitLab API clients
- [x] Official GitHub and GitLab APIs with the developer's credentials
- [x] Read repository and branch info, MRs, pipelines, jobs, environments/deployments
- [x] Credentials come from a secure store or env, and never land in specs, Git or dashboard logs (§10)

### P4-02 — MR creation through AL
- [x] AL creates the branch + PR/MR as part of the authorized run
- [x] The harness records the MR reference against the run and spec version
- [x] **No automatic merge** into protected branches. Repository protections are respected

### P4-03 — Pipeline & CI job monitoring
- [x] Track PR/MR status, workflow/pipeline status, per-job results and test results (AC 22)
- [x] Emit delivery events into the activity feed

### P4-04 — Failure routing
- [x] Classify each failure as **technical** (send to AL for correction) or **design change** (send to the developer for approval)
- [x] Record retries. Avoid infinite fix loops (bounded retries, then Blocked)

### P4-05 — Staging deployment detection
- [x] Detect when the change is deployed to staging (AC 23)
- [x] Expose a "staging ready" signal for Phase 5

### P4-06 — Delivery panel in the dashboard
- [x] MR ref + status, pipeline status, CI job results, staging status, browser validation status (placeholder), remaining blockers (§8.8)

### P4-07 — Command Safety Controller
- [x] Classify commands as safe read-only (auto-run), potentially mutating (needs approval) or unknown/high-risk (needs approval or a verified read-only alternative) (§4)
- [x] Classify by actual side effects, not by command name
- [x] Claude Code / Codex auto-execution modes cannot bypass the harness rules
- [x] Audit record for every significant operation (§11)

### P4-08 — Read-only diagnostics
- [x] Logs, container/service status, config, Git history, CI results, monitoring reads
- [x] Read-only DB access: read-only credentials, restricted permissions, query validation, timeouts and resource limits. `SELECT` alone is **not** proof of safety (§4)
- [x] Redact sensitive output

### P4-09 — Phase 4 acceptance
- [x] Scenario: break a pipeline on purpose with a fixable error → AL finds and fixes it → final status (success or Blocked) is reported correctly
- [x] A mutating command before approval is refused or sent for approval
- [x] No regressions in Phases 1–3. Docs and demo

## Phase exit checklist
- [x] All tasks done, all tests green
- [x] Demo recorded
- [x] Docs updated

Validation and repeatable demonstration: [Phase 4 demo](../docs/demo-phase-4.md). External GitLab behavior is exercised against the local synthetic Postman service; live project permissions and staging policy are deployment-specific.

Session clarification: both GitHub and GitLab are supported. GitHub Actions observations aggregate the latest attempt of every observed workflow for the exact candidate; check summaries retain reporter output without inventing test counts.
