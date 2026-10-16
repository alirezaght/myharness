# myharness — AL Engineering Harness

A **spec-driven AI engineering harness with a human-in-the-loop approval gate**, built to run inside Claude Code in front of the existing AL Orchestrator.

The harness gets rid of ambiguity before any code is written. It reads the codebase, interviews the developer, proposes solutions, draws the architecture, records decisions, and produces a full implementation spec. Only after explicit approval does it hand the work to AL, then it watches the run through to merge request, CI/CD and staging validation.

> **Investigate first. Ask precisely. Design collaboratively. Approve explicitly. Implement through AL. Validate and report.**

## Who owns what

| | Harness | AL Orchestrator |
|---|---|---|
| Requirements discovery | ✅ | |
| Architecture & design (with the developer) | ✅ | |
| Test plan | ✅ proposes, developer approves | implements & runs |
| Approval gates | ✅ | |
| Implementation planning, agents, code | | ✅ |
| Technical review, MR, CI fixes | | ✅ |
| Supervision, monitoring, reporting | ✅ | emits structured events |

The harness **does not** replace AL and must not become a competing orchestrator.

## Core rules

1. The developer owns all significant architecture and design decisions.
2. The harness never invents requirements or silently makes design decisions.
3. Recommendations are welcome, but they need approval.
4. Investigate existing code and earlier decisions before asking questions.
5. No implementation without an approved spec.
6. AL implements inside the approved architecture.
7. New design ambiguity goes back to the developer. Only the affected task blocks, and independent work continues.
8. Every significant decision is traceable and versioned.
9. Tests verify real behaviour. Mocks are allowed only when technically justified.

## Workflow (11 steps)

```
/harness
  → 1  Repository investigation (read-only)
  → 2  Requirements interview (adaptive depth)
  → 3  Architecture & scaffolding design
  → 4  Visual architecture review (diagrams + comments)
  → 5  Test planning
  → 6  Final design review (mandatory completeness check)
  → 7  Final approval ("Approve & Implement", immutable spec snapshot)
  → 8  AL Orchestrator execution (structured events, blockers)
  → 9  Merge request & CI/CD monitoring
  → 10 Staging browser validation (separately approved)
  → 11 Completion summary
```

## Storage model

- **Specs & approved decisions**: versioned Markdown in the project repo, shared via Git.
- **Local runtime state**: SQLite per developer (task state, interview progress, agent events, resume checkpoints). Never shared.
- **Credentials**: never in Markdown, never in Git, never in dashboard logs.

## Repository layout

| Path | Contents |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | Full **Design Specification V1**: behaviour, architecture responsibilities, security, 30 acceptance criteria |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | The 5 delivery phases, estimates, Definition of Done, risks & open questions |
| [`tasks/`](tasks/) | One task checklist per phase |

## Delivery phases

| # | Phase | Estimate | What you get | Tasks |
|---|---|---:|---|---|
| 1 | Core Harness | 4–6 days | Interview → Markdown spec → decision memory, CLI approval, resume | [tasks/phase-1-core-harness.md](tasks/phase-1-core-harness.md) |
| 2 | Visual Design Studio | 3–5 days | Local web UI, diagrams, comments, approval button | [tasks/phase-2-visual-design-studio.md](tasks/phase-2-visual-design-studio.md) |
| 3 | AL Integration & Live Monitoring | 4–7 days | Approved spec → AL run, live agent dashboard, blocking | [tasks/phase-3-al-integration.md](tasks/phase-3-al-integration.md) |
| 4 | GitLab & Delivery Automation | 3–5 days | MR, pipeline monitoring, failure routing, command safety | [tasks/phase-4-gitlab-delivery.md](tasks/phase-4-gitlab-delivery.md) |
| 5 | Staging Validation & Production-Ready V1 | 6–7 days | Browser acceptance tests on staging, hardening, docs | [tasks/phase-5-staging-validation.md](tasks/phase-5-staging-validation.md) |
| | **Total** | **20–30 working days** | **Full Harness V1** | |

Each phase ships something you can run on a real task, and no phase breaks the ones before it.

## Status

Design spec V1 is drafted and **awaiting final approval**. Implementation has not started. Next step: Phase 1, task `P1-01` (technology decision).
