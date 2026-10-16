# Phase 3 — AL Integration & Live Monitoring

**Goal:** after approval the harness launches the existing AL Orchestrator with the approved spec, then shows agents, progress, dependencies and blockers live. New design decisions block only the affected task.
**Estimate:** 4–7 working days · **Roadmap:** [docs/ROADMAP.md](../docs/ROADMAP.md#phase-3--al-integration--live-monitoring) · **Spec refs:** §3 (step 8), §8.2–8.5, §8.11, §9, §12
**Depends on:** Phase 1, Phase 2

> ⚠️ Start with P3-01. If AL cannot emit structured events, add work to AL itself and re-estimate.

## Tasks

### P3-01 — AL capability audit
- [ ] Find out what AL exposes today: structured output or terminal only, agent lifecycle, progress, blockers, resumability
- [ ] Write a gap list against the §9 integration contract
- [ ] Decide what changes AL needs, and get them approved

**Done when:** an approved integration decision is recorded.

### P3-02 — AL event contract
- [ ] Define the event schema: run started/ended, agent lifecycle, task assigned/progress/completed, blocker raised/resolved, test results, implementation result, MR/pipeline/deploy updates
- [ ] Every event carries run id + approved spec version
- [ ] Versioned contract doc in `docs/`

### P3-03 — AL changes (if needed)
- [ ] Make AL emit the structured events (preferred over parsing terminal output)
- [ ] AL reports new architecture or product decisions as blockers instead of deciding them itself (AC 29)
- [ ] Do not reimplement or fork orchestration logic

### P3-04 — AL Integration Adapter
- [ ] Launch AL with the approved spec + test plan + spec version (AC 18)
- [ ] Ingest events into SQLite
- [ ] Reconnect to or recover an existing run, without re-launching completed work

### P3-05 — Blocker handling
- [ ] A blocker puts the affected task into `Blocked` and creates an entry in the Approval Center
- [ ] Independent tasks keep running
- [ ] Developer decision → recorded in the Decision Registry → sent back to AL → task unblocks

**Done when:** a simulated new-decision blocker stops one task while a sibling task completes.

### P3-06 — Live updates transport
- [ ] Push updates to the dashboard (SSE or WebSocket), with no manual refresh
- [ ] Handle disconnects gracefully. Clearly mark stale or unavailable data (§8.11)

### P3-07 — Agent monitoring view
- [ ] Agent count. For each agent: name, role, assigned task, activity, status, progress, duration, dependencies, blockers, latest output (§8.2)
- [ ] Statuses: Idle, Running, Waiting, Blocked, Failed, Completed

### P3-08 — Progress tracking
- [ ] Overall and per-agent progress. Completed, pending and blocked tasks. Current phase (§8.3)
- [ ] Progress comes **only** from measurable completion or explicitly labelled estimates, never made up

### P3-09 — Orchestration view & activity feed
- [ ] Graph of agents, parallel work, ownership, dependencies, handoffs, blocked and completed paths (§8.4)
- [ ] Live feed of meaningful events, with detailed logs on demand (§8.5)

### P3-10 — Execution traceability
- [ ] Every run is linked to the exact approved spec version (AC 30)
- [ ] A run cannot start against a draft or superseded spec

### P3-11 — Phase 3 acceptance
- [ ] Scenario: approve spec → AL starts → agents visible live → a new architecture decision blocks only that part and waits for the developer
- [ ] Kill the dashboard or harness mid-run and reconnect without losing state
- [ ] No regressions in Phases 1–2. Docs and demo

## Phase exit checklist
- [ ] All tasks done, all tests green
- [ ] Demo recorded
- [ ] Docs updated
