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
| [`docs/decisions/`](docs/decisions/) | Approved decision records for the harness itself |
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

## Development

```bash
uv sync                    # install Python 3.13 and dependencies
uv run pytest              # run tests
uv run ruff check .        # lint
uv run ruff format .       # format
uv run harness --version   # run the CLI
uv run harness start "<request>"             # create a task (the only entry point)
uv run harness phase <id> next               # advance one phase
uv run harness phase <id> back <phase> --reason "<why>"   # return to an earlier phase
uv run harness classify <id> <level>         # override complexity (trivial|simple|standard|complex)
uv run harness show <id>                     # task, interview topics and phase history
uv run harness investigate <id> [--repo PATH] [--json]   # read-only repository investigation (facts / assumptions / questions)
```

Global options go before the subcommand, e.g. `uv run harness --db /tmp/state.db show <id>`.

### Local state

Task state lives in a per-developer SQLite database outside the repository, at `$HARNESS_HOME/projects/<hash of repo root>/state.db` (`HARNESS_HOME` defaults to `~/.local/share/harness`). Set `HARNESS_DB` or pass `--db PATH` to use a specific file. Only `harness start` creates state; `--version`, `--help` and importing the package never write anything.

The database holds tasks, phase history, interview questions and answers (including open questions), proposals, events and checkpoints. Each task owns its own rows, so one task's state is isolated from another's, and state survives a process restart. On POSIX the database file is created with mode `0600`, and its directory with `0700` when the harness created it or it lies under `$HARNESS_HOME/projects`. An existing directory you point `HARNESS_DB` or `--db` at is left as it is. Migrations are applied automatically when the database is opened.

### Investigation

`harness investigate <id>` scans the target repository (the git top-level of the current directory, or `--repo PATH`) without writing anything to it, not even under `.git/`. It first loads the approved decision records from `docs/decisions`, then reports relevant files, components, dependencies, interfaces, schemas, config, tests and conventions, keeping **verified facts** (with evidence), **assumptions** (with their basis) and **unresolved questions** in separate sections. Contents of secret-bearing files such as `.env` are never read. Results are stored in the task workspace: the open questions, an `investigation` checkpoint and an `investigation.completed` event. Re-running adds a new checkpoint but no duplicate questions.

## Status

Design spec approved for implementation; P1-01 to P1-04 done (stack: Python 3.13 / uv / stdlib sqlite3 / pytest + ruff / GitHub Actions; `/harness` activation, phase state machine, task classification and local SQLite state with per-task workspaces, read-only repository analyzer). Next task: P1-05.
