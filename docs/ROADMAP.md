# Implementation Roadmap — AL Engineering Harness V1

This roadmap is kept **separate from the design spec** ([SPEC.md](SPEC.md)). Hand one phase at a time to Claude Code / AL, check what comes out, and move on only after that phase passes its acceptance check.

**Guiding principle:** the harness should run on a real task from Phase 1 onward. Each phase adds a capability without breaking the earlier ones.

## Current status — 23 October 2026

[Current and future AL, Harness and interaction diagrams](https://chatgpt.com/space/page_11adcd18b4ac8191904172a708044954) are available in the private roadmap page.

Phases 1 and 2 were implemented before this work. Phase 3 is implemented and verified: AL execution and monitoring, the mandatory managed launcher with active-runtime pinning, artifact consumer routing, declared review-hook state and baseline exceptions are complete, and complete AL verification passes every collected test (see docs/handoffs/2026-10-23-phase3-status.md). The former 360-second complete-suite target was dropped by decision: `release_verify --full` takes about 12 minutes and its status reflects test results only, and the 13 slowest end-to-end proofs run as a separate acceptance tier (`--tier acceptance`) at release time.

Live native runs on Claude and Codex reach an approved AL result. AL is installed globally and repository-local AL copies were removed; the guarded single installation entrypoint exists but was bypassed for this install, so global updates are currently a manual copy.

Phase 3 needs no further implementation estimate; Phases 4 and 5 remain future work. All phase and component estimates below are the original planning estimates, not a new remaining-work commitment.

```
 ┌────────┐   ┌────────┐   ┌────────┐   ┌──────────┐   ┌────────────┐
 │ 1 Core │ → │2 Visual│ → │  3 AL  │ → │4 Delivery│ → │5 Validation│
 └────────┘   └────────┘   └────────┘   └──────────┘   └────────────┘
   4–6 d        3–5 d        4–7 d        3–5 d           6–7 d        ≈ 20–30 working days
```

---

## Phase 1 — Core Harness

**Estimate:** 4–6 working days · **Priority:** essential · **Tasks:** [phase-1-core-harness.md](../tasks/phase-1-core-harness.md)

**Goal:** turn a simple or complex request into a complete, approved specification.

Capabilities:
- Manual activation via `/harness`
- Read-only investigation of the codebase and earlier decisions
- Adaptive interview, with question depth matched to task complexity
- Solution proposals without unilateral decisions
- Definition of components, interactions, schemas and configuration
- Markdown spec generation
- Test plan and acceptance criteria
- Final review for contradictions and open decisions
- Approved decisions stored in Git, local state in SQLite
- Resume

**Deliverable:** a working harness in Claude Code that produces an approved spec for a feature, bug fix or refactor. Approval happens through the CLI, and AL is **not** launched yet.

**Acceptance check:** take a real task from start to spec, interrupt the interview halfway, resume it, and end with a spec that has no unresolved significant decision.

## Phase 2 — Visual Design Studio

**Estimate:** 3–5 working days · **Priority:** high · **Tasks:** [phase-2-visual-design-studio.md](../tasks/phase-2-visual-design-studio.md)

**Goal:** turn architecture design from a text conversation into a visual review.

Capabilities:
- Lightweight local web UI
- View the spec and the decisions made so far
- Component, sequence and data-flow diagrams when relevant
- Select components and connections
- Comment and request a redesign
- Generate revised diagrams
- Show open decisions, plus a final approval button
- Version history for specs and diagrams

**Deliverable:** a web page where you can see the task's architecture, comment on a wrong connection, and approve the revised version.

**Acceptance check:** select a diagram with a wrong connection, comment on it, and the harness fixes it. Final approval stays disabled until every open comment is resolved.

## Phase 3 — AL Integration & Live Monitoring

**Estimate:** 4–7 working days · **Priority:** essential · **Tasks:** [phase-3-al-integration.md](../tasks/phase-3-al-integration.md)

**Goal:** after approval, the harness launches AL and you can watch all activity live.

Capabilities:
- Direct connection to the existing AL Orchestrator
- Pass the approved spec and test plan
- Show the number of agents and their tasks
- Show Running / Waiting / Blocked / Failed / Completed statuses
- Show real (or explicitly estimated) progress per task
- Show parallel execution and dependencies
- Live activity feed
- Block only the part that depends on an unresolved decision
- Independent work continues
- Persist execution state and recover after a disconnect

**Deliverable:** a full flow from spec approval to AL execution, with live monitoring in the web UI.

**Acceptance check:** approve a spec, AL starts, and you can see agent status. When a new architecture decision becomes necessary, only that part blocks and waits for your decision.

> ⚠️ This phase may require changes to AL itself so that it emits structured events.

## Phase 4 — GitLab & Delivery Automation

**Estimate:** 3–5 working days · **Priority:** high · **Tasks:** [phase-4-gitlab-delivery.md](../tasks/phase-4-gitlab-delivery.md)

**Goal:** an AL run ends in a real merge request and a pipeline you can track.

Capabilities:
- GitLab API connection
- Branch and MR creation through AL
- MR link and status display
- Pipeline and CI job monitoring
- Failure detection and routing back to AL
- Staging deployment detection
- Delivery status in the dashboard
- Read-only command control, plus approval requests for high-risk commands
- No automatic merge into protected branches

**Deliverable:** a real change in a repo that AL implements, tests and opens as an MR, with pipeline status visible in the dashboard.

**Acceptance check:** break a pipeline on purpose with a fixable error. AL finds and fixes it, and the final status (success or Blocked) is reported correctly.

## Phase 5 — Staging Validation & Production-Ready V1

**Estimate:** 6–7 working days · **Priority:** V1 completion · **Tasks:** [phase-5-staging-validation.md](../tasks/phase-5-staging-validation.md)

**Goal:** after deployment, the real result is validated and the whole system is reliable.

**Implementation status (2026-10-26):** the local browser plan/approval/controller,
read-only reporting/dashboard, recovery and security coverage, 30-criterion
evidence map, fixture demonstration, and installation/usage documentation are
implemented. Focused ledger checks for those areas are recorded.

**Release status:** the production browser capability and desktop/mobile visual
checks are not verified in this environment because available Chrome binaries
failed signature validation. No live staging target was requested for this
session, so real staging acceptance is outside this run. The Phase 5/V1 exit
and “all tests green” checks remain incomplete; see the task checklist for
implementation versus release evidence.

Capabilities:
- Identify the acceptance criteria that can be tested in a browser
- Request approval before browser testing
- Connect to a compatible browser automation tool
- Test against the real staging environment
- Record pass/fail and evidence
- Route problems back to AL
- Full final report
- Test recovery, retry and protection against repeated side effects
- Test permissions, command safety and read-only database access
- Installation, configuration and usage docs

**Deliverable:** a V1 that handles the whole path: request → spec → approval → implementation → MR → CI/CD → staging validation.

**Acceptance check:** run a real feature end to end, test it on staging, and end with a report you can rely on that covers test results, the MR and the pipeline.

---

## Summary

| Phase | Time | What you actually get |
|---|---:|---|
| 1 | 4–6 days | Interview + spec + memory |
| 2 | 3–5 days | Web UI + diagrams + approval |
| 3 | 4–7 days | AL execution + live agent monitoring |
| 4 | 3–5 days | MR + pipeline + GitLab |
| 5 | 6–7 days | Staging testing + stable V1 |
| **Total** | **20–30 days** | **Full Harness V1** |

The estimate assumes AI-assisted development by one developer who knows the project. The real duration depends on AL's current capabilities, GitLab access and browser tool availability. A version several developers can rely on could realistically take **6–8 weeks**.

### Alternative grouping (3 stages)

| Stage | Duration | Scope |
|---|---|---|
| Working MVP | ~1 week | Interview, codebase investigation, spec, decision memory, final approval, basic AL hookup |
| Visual Harness | 1–2 weeks | Web UI, diagrams, comments, SQLite, resume, agent status and progress |
| Full Automation | 1–2 weeks | GitLab, pipeline monitoring, staging tests, command control, error handling, hardening |

### Per-component estimates (overlapping, so don't add them up)

| Component | Estimate |
|---|---|
| Harness Controller & phase management | 1–2 days |
| Repository Analyzer & Interview Engine | 3–5 days |
| Spec, Approval & Decision Memory | 2–3 days |
| SQLite & resume | 1–2 days |
| Diagram generation & commenting | 2–4 days |
| Web UI & dashboard | 3–5 days |
| AL connection & agent monitoring | 3–6 days |
| GitLab, MR & pipeline monitoring | 2–3 days |
| Read-only commands & security | 2–3 days |
| Browser testing on staging | 2–4 days |
| Final testing & fixes | 3–5 days |

---

## Definition of Done (every phase)

**No phase is Done unless its output can actually be run and tested.**

- [ ] The phase's tests pass
- [ ] Capabilities from earlier phases still work (no regressions)
- [ ] Docs are updated
- [ ] There is a real demo of the phase's output

**Dogfooding:** after Phase 1, run the harness on itself and ask it to extract the requirements for Phase 2. This is the best early test of interview and spec quality.

---

## Risks & open questions

1. **Interview completeness (hardest part).** How do we know every significant decision was made and nothing was invented? A confidence number alone is not reliable. The interview needs a **structured checklist of unresolved decisions** that blocks the final review.
2. **AL structured events.** Does AL already emit machine-readable agent status, progress and blockers, or only terminal output? The answer can shift the estimate by several days to a week. The spec treats structured events as a *requirement*, not an assumption.
3. **Browser automation access.** Whether Claude in Chrome, Codex browser automation or another tool can actually be driven against staging still needs to be verified.
4. **Technology choices are deliberately open.** Backend/frontend frameworks, DB tables, the diagram renderer and the command parser are not fixed by the spec (see SPEC §15). They are decided in Phase 1 (`P1-01`).
