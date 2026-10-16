# Phase 1 — Core Harness

**Goal:** turn a request into a complete, approved Markdown spec through the CLI, with decision memory and resume. AL is not launched in this phase.
**Estimate:** 4–6 working days · **Roadmap:** [docs/ROADMAP.md](../docs/ROADMAP.md#phase-1--core-harness) · **Spec refs:** §1–3 (steps 1–3, 5–7), §5, §6, §7, §13

## Tasks

### P1-01 — Technology & project skeleton decision
- [ ] Decide the runtime and language, the packaging (Claude Code skill/command plus a local service?), the SQLite library and the test framework
- [ ] Record each choice as an approved decision (dogfoods P1-07)
- [ ] Create the project skeleton, lint/format and a test runner that runs in CI

**Done when:** the skeleton builds, an empty test passes, and the decisions are written down.

### P1-02 — `/harness` activation & Harness Controller
- [ ] `/harness <request>` is the only entry point, with no implicit activation (AC 1)
- [ ] Phase state machine: investigation → interview → design → test plan → final review → approval
- [ ] Phase transitions are explicit and persisted. Going back to an earlier phase is allowed
- [ ] Task classification (trivial → complex) drives interview depth (AC 3)

**Done when:** a task can be created and stepped through every phase, and invalid transitions are rejected.

### P1-03 — Local State Manager (SQLite)
- [ ] Schema for tasks, phase state, interview Q&A, open questions, proposals, events and checkpoints
- [ ] Each task has its own isolated workspace (§7)
- [ ] The DB lives outside Git and is private to the developer (AC 14, 27)
- [ ] Migrations

**Done when:** state survives a process restart, and tests cover reading and writing every entity.

### P1-04 — Repository Analyzer (read-only)
- [ ] Find the relevant components, files, dependencies, interfaces, schemas, config, tests and conventions
- [ ] Load previously approved decisions before asking anything (AC 2)
- [ ] Output keeps **verified facts**, **assumptions** and **unresolved questions** separate
- [ ] Guarantee: no writes to the repo during investigation

**Done when:** on a sample repo it produces a fact/assumption/question report, and a test proves the repo is unchanged afterwards.

### P1-05 — Interview Engine
- [ ] Adaptive questions covering business, functional, I/O, edge cases, components, contracts, schemas, config, errors, performance, security, observability and AC
- [ ] Questions can be asked one at a time or in related groups
- [ ] When a decision is needed, present a recommendation with its rationale. The developer accepts, rejects or modifies it (AC 5)
- [ ] "I don't know" or an unanswered question stays **unresolved**, never filled with an assumption (AC 4)
- [ ] Skip questions already answered by approved decisions or by the codebase
- [ ] **Structured unresolved-decision checklist** (main quality risk, see ROADMAP Risks #1)

**Done when:** a scripted interview run leaves the right items unresolved, and no requirement shows up in the spec that the developer did not provide or approve.

### P1-06 — Architecture Designer (text)
- [ ] Capture components, responsibilities, interactions, data flow, interfaces/contracts, config, failure handling and required behaviour
- [ ] Mark what is delegated to AL (class/method names, file placement) versus what is fixed by the design

**Done when:** the design section of the spec covers every item in SPEC §3, step 3.

### P1-07 — Decision Registry (Git-versioned Markdown)
- [ ] Decision record: id, content, rationale, scope, status, version, timestamp, supersedes, impact (§6.4)
- [ ] Automatic recording on approval
- [ ] Changing a decision shows the original rationale and the impact, and requires approval
- [ ] The latest version is shown by default. History stays accessible (AC 13, 15)
- [ ] Cross-project knowledge only produces suggestions and never auto-applies (§6.3)

**Done when:** superseding a decision keeps the full chain, and a fresh clone can read every approved decision.

### P1-08 — Specification Manager
- [ ] Markdown spec template: requirements, architecture, components/interfaces, config, AC, test plan, rationale, constraints
- [ ] States: draft → approved → superseded
- [ ] Each approved spec is linked to its version and repo revision (AC 30)

**Done when:** an approved spec is an immutable file tied to a commit.

### P1-09 — Test Planner
- [ ] Propose only the relevant unit, integration, E2E, regression, edge-case and failure tests
- [ ] The developer can add, remove or modify scenarios, and approves the plan (AC 9)
- [ ] Mocks only with a written justification. Flag any mock that hides missing behaviour

**Done when:** the approved test plan is embedded in the spec.

### P1-10 — Final Design Review (completeness gate)
- [ ] Check every item in SPEC §3, step 6 (clear requirements, decisions approved, consistency, testable AC, no blocking questions, no ignored conflicts)
- [ ] When something fails, route back to the right step (AC 10)

**Done when:** a spec with even one open blocking item cannot reach approval.

### P1-11 — CLI approval gate
- [ ] Explicit approval command that produces an immutable, versioned spec snapshot
- [ ] Block every implementation path before approval (AC 11)

**Done when:** a test proves nothing can be handed off without an approval record.

### P1-12 — Resume
- [ ] Resume an interrupted task from its last consistent checkpoint (AC 12)
- [ ] List the tasks that can be resumed

**Done when:** killing the process mid-interview and resuming continues from the same question.

### P1-13 — Phase 1 acceptance & dogfooding
- [ ] Run a real task end to end, interrupt, resume, and end with a spec that has no unresolved significant decision
- [ ] Dogfood: use the harness to produce the spec for Phase 2
- [ ] Update the README/docs, record a demo

## Phase exit checklist
- [ ] All tasks done, all tests green
- [ ] Demo recorded
- [ ] Docs updated
