# Phase 3 — AL Integration & Live Monitoring

**Goal:** after approval the harness launches the existing AL Orchestrator with the approved spec, then shows agents, progress, dependencies and blockers live. New design decisions block only the affected task.
**Estimate:** 4–7 working days · **Roadmap:** [docs/ROADMAP.md](../docs/ROADMAP.md#phase-3--al-integration--live-monitoring) · **Spec refs:** §3 (step 8), §8.2–8.5, §8.11, §9, §12
**Depends on:** Phase 1, Phase 2

**Accepted integration decision:** [DEC-0019](../docs/decisions/DEC-0019-native-cli-al-integration.md).
**Event protocol:** [AL execution event contract v1](../docs/al-event-contract-v1.md).
**Status (2026-10-24):** complete. Every task below is implemented; complete AL verification passes every collected test, and live native runs on both hosts reached an approved AL result (see [the Phase 3 status](../docs/handoffs/2026-10-23-phase3-status.md)).

> ⚠️ Start with P3-01. If AL cannot emit structured events, add work to AL itself and re-estimate.

## Tasks

### P3-01 — AL capability audit
- [x] Find out what AL exposes today: structured output or terminal only, agent lifecycle, progress, blockers, resumability
- [x] Write a gap list against the §9 integration contract
- [x] Decide what changes AL needs, and get them approved

**Done when:** an approved integration decision is recorded.

### P3-02 — AL event contract
- [x] Define the event schema: run started/ended, agent lifecycle, task assigned/progress/completed, blocker raised/resolved, test results, implementation result, MR/pipeline/deploy updates
- [x] Every event carries run id + approved spec version
- [x] Versioned contract doc in `docs/`

### P3-03 — AL changes (if needed)
- [x] Make AL emit the structured events (preferred over parsing terminal output)
- [x] AL reports new architecture or product decisions as blockers instead of deciding them itself (AC 29)
- [x] Do not reimplement or fork orchestration logic

### P3-04 — AL Integration Adapter
- [x] Launch AL with the approved spec + test plan + spec version (AC 18)
- [x] Ingest events into SQLite
- [x] Reconnect to or recover an existing run, without re-launching completed work

### P3-05 — Blocker handling
- [x] A blocker puts the affected task into `Blocked` and creates an entry in the Approval Center
- [x] Independent tasks keep running
- [x] Developer decision → recorded in the Decision Registry → sent back to AL → task unblocks

**Done when:** a simulated new-decision blocker stops one task while a sibling task completes.

### P3-06 — Live updates transport
- [x] Push updates to the dashboard (SSE or WebSocket), with no manual refresh
- [x] Handle disconnects gracefully. Clearly mark stale or unavailable data (§8.11)

### P3-07 — Agent monitoring view
- [x] Agent count. For each agent: name, role, assigned task, activity, status, progress, duration, dependencies, blockers, latest output (§8.2)
- [x] Statuses: Idle, Running, Waiting, Blocked, Failed, Completed

### P3-08 — Progress tracking
- [x] Overall and per-agent progress. Completed, pending and blocked tasks. Current phase (§8.3)
- [x] Progress comes **only** from measurable completion or explicitly labelled estimates, never made up

### P3-09 — Orchestration view & activity feed
- [x] Graph of agents, parallel work, ownership, dependencies, handoffs, blocked and completed paths (§8.4)
- [x] Live feed of meaningful events, with detailed logs on demand (§8.5)

### P3-10 — Execution traceability
- [x] Every run is linked to the exact approved spec version (AC 30)
- [x] A run cannot start against a draft or superseded spec

### P3-11 — Phase 3 acceptance
- [x] Scenario: approve spec → AL starts → agents visible live → a new architecture decision blocks only that part and waits for the developer
- [x] Kill the dashboard or harness mid-run and reconnect without losing state
- [x] No regressions in Phases 1–2. Docs and demo

## Phase exit checklist
- [x] All tasks done, all tests green
- [x] Demo recorded
- [x] Docs updated

### Candidate evidence and remaining exit conditions

Service admission also requires a coverage decision for every potentially
affected service and its required checks. Identified dependencies select the
affected services; uncertainty may use exhaustive coverage only when independent
service enumeration and check discovery are complete; missing or unsupported
coverage blocks with named gaps. Generic policy is shared across repositories,
with repository-specific discovery expressed only through a repository's own
declared `.al/repository.json` contract. See the shipped AL
reference `service-coverage.md` and `schemas/service-coverage.json` in both
platform packages. The current ledger is the authority for validation status.

The deterministic acceptance uses the real harness approval, detached worker,
AL lifecycle and decision/acknowledgement path, plus dashboard reconnect; only
the external provider executable/model boundary is simulated. The standalone demo and browser functional/visual acceptance are recorded in
the guarded ledger. Browser captures cover decision controls, execution
summary/agents, and task graph at desktop 1280×800 and mobile 390×844; measured
document widths matched the viewport widths. The graph uses an intentional
horizontal scroller on mobile. Consult the current ledger and its artifacts for
validation status; historical EIDs are not current evidence.

Live native-host acceptance passed on 2026-10-24 with `scripts/smoke_phase3_native.py`, each run in a disposable project through the candidate launcher: Claude (AL run `761897ab`, 23 minutes) and Codex (AL run `fdfdc1a5`, 13 minutes) each went from harness approval through AL planning, implementation, gates, independent fresh and comparison reviews to an approved AL result, and the harness recorded the execution as `Completed`. Earlier attempts (E105, E106 and four bounded 2026-10-23/24 runs) found the AL defects fixed on the way: writer lifetimes opened late, rework could not reopen a completed task, and a scopeless preflight was refused in a harness-bound run.
