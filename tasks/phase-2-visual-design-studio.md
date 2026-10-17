# Phase 2 — Visual Design Studio

**Goal:** a local web UI where the developer reviews the spec and diagrams, comments on components or connections, gets redesigns, and approves with one button.
**Estimate:** 3–5 working days · **Roadmap:** [docs/ROADMAP.md](../docs/ROADMAP.md#phase-2--visual-design-studio) · **Spec refs:** §3 (steps 4, 7), §8.1, §8.6, §8.7, §8.9, §13 (Diagram Service, Web Dashboard)
**Depends on:** Phase 1

## Tasks

### P2-01 — Local web app skeleton
- [x] Lightweight local server plus frontend, launched from `/harness` (opens in the browser)
- [x] Reads and writes the same SQLite state and Markdown specs as the CLI
- [x] Binds to localhost only

**Done when:** `/harness` opens the dashboard for the active task.

### P2-02 — Task overview & multi-task switching
- [x] Task name, description, current phase, status, elapsed time, pending approvals, latest activity (§8.1)
- [x] List of tasks, switching between them, resuming interrupted tasks (§8.9)

### P2-03 — Spec & decisions view
- [x] Render the current spec Markdown
- [x] Show approved decisions and **open decisions** separately
- [x] Version history for the spec (diff between versions)

### P2-04 — Diagram Service
- [x] Generate only the relevant diagrams: component, sequence, data-flow, state, deployment
- [x] Diagrams come from a structured model (stable ids for components and connections) so comments can be anchored to them
- [x] Version history for diagrams

**Done when:** a sample design renders all relevant diagram types with addressable elements.

### P2-05 — Interactive commenting
- [x] Select a component or connection and add a comment (clarification / correction / redesign)
- [x] Comments persist in task state and are tied to the element id and the diagram version
- [x] Comment states: open → addressed → resolved

### P2-06 — Redesign loop
- [x] The harness takes open comments, updates the design and regenerates the diagrams
- [x] Show revised diagrams next to the previous version, with the change highlighted
- [x] The developer resolves or re-opens each comment (AC 8)

### P2-07 — Approval Center & "Approve & Implement" button
- [x] One place for every pending approval: decisions, test plan, final spec (§8.7)
- [x] The button stays disabled while comments are open, blocking questions remain or the final review fails
- [x] Approval writes the same immutable snapshot as the Phase 1 CLI gate

### P2-08 — Phase 2 acceptance
- [ ] Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved
- [ ] No regressions in the Phase 1 CLI flow
- [ ] Docs and demo

## Phase exit checklist
- [ ] All tasks done, all tests green
- [ ] Demo recorded
- [ ] Docs updated
