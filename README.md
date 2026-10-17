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
| [`docs/decisions/`](docs/decisions/) | Approved decision records: DEC-0001 to DEC-0004 for the harness itself, DEC-0005 to DEC-0018 from the Phase 2 dogfood run |
| [`docs/specs/`](docs/specs/) | Sealed, versioned specifications; [`t-a3752edf/v1.md`](docs/specs/t-a3752edf/v1.md) is the Phase 2 spec |
| [`docs/demo.md`](docs/demo.md) | Recorded Phase 1 demo transcript, kept in sync with `scripts/demo.py` by `tests/test_demo.py` |
| [`docs/dogfood/`](docs/dogfood/) | Transcript of the run that produced the Phase 2 spec |
| [`harness/dashboard/`](harness/dashboard/) | The Phase 2 local web dashboard: stdlib HTTP server, read model, launcher and static HTML/JS assets |
| [`scripts/`](scripts/) | `demo.py`, the throwaway-state Phase 1 demo |
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
uv run python scripts/demo.py   # replay the Phase 1 demo on throwaway state (see docs/demo.md)
uv run harness --version   # run the CLI
uv run harness start "<request>"             # create a task (the only entry point)
uv run harness phase <id> next               # advance one phase
uv run harness phase <id> back <phase> --reason "<why>"   # return to an earlier phase
uv run harness classify <id> <level>         # override complexity (trivial|simple|standard|complex)
uv run harness show <id>                     # task, interview topics and phase history
uv run harness investigate <id> [--repo PATH] [--decisions PATH] [--json]   # read-only repository investigation (facts / assumptions / questions)
uv run harness interview next <id> [--group]   # ask the next open question (or every open question of its topic)
uv run harness interview answer <id> <qid> <text>   # record the developer's answer
uv run harness interview unknown <id> <qid>   # "I don't know": the question stays open
uv run harness interview skip <id> <qid> --source decision|codebase|not_relevant --ref <ref>   # skip with provenance
uv run harness interview ask <id> <topic> <text> [--decision]   # add a follow-up question
uv run harness interview propose <id> <qid> --title T --recommendation R --rationale W [--impact I]   # attach a recommendation
uv run harness interview decide <id> <pid> accept|reject|modify [--response TEXT] [--decisions PATH]   # developer's verdict (records a decision question's approval)
uv run harness interview checklist <id> [--json]   # unresolved-decision checklist (read-only)
uv run harness interview summary <id> [--json]   # requirements with provenance (read-only)
uv run harness design add <id> <section> <text...> [--delegated]   # record a design element the developer stated
uv run harness design none <id> <section> --reason R   # the developer states a section does not apply
uv run harness design propose <id> <section> <text...> --rationale W [--delegated]   # attach a recommendation
uv run harness design decide <id> <eid> accept|reject|modify [--response TEXT]   # developer's verdict
uv run harness design withdraw <id> <eid>   # withdraw an effective design entry
uv run harness design show <id> [--json]   # design section with provenance (read-only)
uv run harness design coverage <id> [--json]   # uncovered and unresolved design items (read-only)
uv run harness testplan add <id> <category> <text...> [--covers Q1|E1 ...] [--mock T --mock-reason W]   # record a test scenario the developer stated
uv run harness testplan propose <id> <category> <text...> --rationale W --covers Q1|E1 [--covers ...] [--mock T --mock-reason W]   # attach a recommended scenario
uv run harness testplan decide <id> <sid> accept|reject|modify [--response TEXT]   # developer's verdict
uv run harness testplan edit <id> <sid> [--text T] [--category C] [--covers REF ...] [--mock T --mock-reason W | --no-mock]   # change an effective scenario
uv run harness testplan withdraw <id> <sid>   # withdraw an effective scenario
uv run harness testplan flag <id> <sid> --reason R [--clear]   # flag a mock that hides missing behaviour, or clear the flag
uv run harness testplan approve <id>   # approve the whole plan (test_plan phase only)
uv run harness testplan show <id> [--json]   # the test plan with provenance (read-only)
uv run harness testplan review <id> [--json]   # undecided, flagged and unapproved items (read-only)
uv run harness decision list [--decisions PATH] [--json]   # latest version of every decision (read-only)
uv run harness decision show <DEC-id> [--history] [--decisions PATH] [--json]   # one decision, --history adds every earlier version (read-only)
uv run harness decision record <id> --title T --decision C --rationale W [--scope S] [--impact I] [--decisions PATH]   # record a decision the developer stated
uv run harness decision change <id> <DEC-id> --decision C --rationale W --impact I [--title T] [--scope S] [--decisions PATH]   # preview a change, writes nothing
uv run harness decision decide <id> <cid> accept|reject|modify [--response TEXT] [--decisions PATH]   # developer's verdict on a proposed change
uv run harness decision suggest <id> --from PATH [--from PATH ...] [--json] [--decisions PATH]   # hints from other repositories' decisions (read-only)
uv run harness spec show <id> [--specs PATH]   # preview the specification from the approved state (writes nothing)
uv run harness spec draft <id> [--specs PATH]   # write the preview to docs/specs/<id>/draft.md
uv run harness spec approve <id> [--specs PATH]   # seal v<N>.md tied to the commit (approval phase only)
uv run harness spec list <id> [--json] [--specs PATH]   # every specification version (read-only)
uv run harness spec verify <id> [--specs PATH]   # check each approved file against its content hash (read-only)
uv run harness approve <id> [--specs PATH]   # Approve & Implement: seal v<N>.md and record the approval that authorizes the handoff (approval phase only)
uv run harness review <id> [--json]   # final design review: SPEC §3 step 6 completeness check (read-only, exit 1 when it blocks)
uv run harness resume [<id>] [--json] [--specs PATH]   # where an interrupted task continues; no id lists resumable tasks (read-only)
uv run python -m harness.dashboard [<id>] [--db PATH] [--specs PATH] [--no-browser]   # local web dashboard for a task (127.0.0.1, free port; writes only diagram versions, comments (each with its event) and the Approve & Implement snapshot)
uv run python -m harness.redesign <id> [--db PATH]   # list the task's open diagram comments (read-only)
uv run python -m harness.redesign address <id> <comment id>... [--db PATH]   # record the design as a diagram version, then mark the comments addressed (all or nothing)
```

Global options go before the subcommand, e.g. `uv run harness --db /tmp/state.db show <id>`.

### Local state

Task state lives in a per-developer SQLite database outside the repository, at `$HARNESS_HOME/projects/<hash of repo root>/state.db` (`HARNESS_HOME` defaults to `~/.local/share/harness`). Set `HARNESS_DB` or pass `--db PATH` to use a specific file. Only `harness start` creates state; `--version`, `--help` and importing the package never write anything.

The database holds tasks, phase history, interview questions and answers (including open questions), proposals, events and checkpoints. Each task owns its own rows, so one task's state is isolated from another's, and state survives a process restart. On POSIX the database file is created with mode `0600`, and its directory with `0700` when the harness created it or it lies under `$HARNESS_HOME/projects`. An existing directory you point `HARNESS_DB` or `--db` at is left as it is. Migrations are applied automatically when the database is opened.

### Investigation

`harness investigate <id>` scans the target repository (the git top-level of the current directory, or `--repo PATH`) without writing anything to it, not even under `.git/`. It first loads the approved decision records from `docs/decisions`, then reports relevant files, components, dependencies, interfaces, schemas, config, tests and conventions, keeping **verified facts** (with evidence), **assumptions** (with their basis) and **unresolved questions** in separate sections. Contents of secret-bearing files such as `.env` are never read. Results are stored in the task workspace: the open questions, an `investigation` checkpoint and an `investigation.completed` event. Re-running adds a new checkpoint but no duplicate questions.

### Interview

`harness interview` runs the requirements interview once the task is in the `interview` phase and has been investigated. The CLI owns the question state and Claude Code (`/harness`) relays the questions and records every response. The catalogue covers all 14 interview topics (business requirements, functional behaviour, inputs/outputs, edge cases, component responsibilities and communication, schemas, API contracts, configuration, error handling, performance, security, observability and acceptance criteria). Only the topics in the task's complexity depth are asked, so a trivial task gets a short interview and a complex one the full set. Changing the complexity re-syncs the catalogue: never-asked extras are skipped as not relevant (or reopened when the depth grows again) and questions already asked are never touched. Investigation questions come first. `next` asks one question at a time, or a whole topic with `--group`.

Provenance rules: a requirement exists only if the developer typed an answer, or accepted (or modified) a recommendation. A decision question needs a recommendation with rationale (`propose`), and the developer accepts, rejects or modifies it. Rejected or pending recommendations never become requirements. A question is skipped only with provenance: an approved decision id from the investigation, or an evidence reference for the codebase. "I don't know" (`unknown`) never becomes an answer: the question stays open and blocking, and `next` moves on to others. `harness interview checklist` lists every unresolved item (never asked, unanswered, awaiting a decision, recommendation rejected) and says whether it is blocking. Due catalogue questions not asked yet appear as `catalogue:<key>`. Every mutating command is one transaction that records an event and an `interview` checkpoint, and fails with exit 1 outside the interview phase.

### Design

`harness design` captures the logical architecture of a task once it is in the `design` phase (SPEC §3, Phase 3). The design has eight sections: `components`, `responsibilities`, `interactions`, `data_flow`, `interfaces` (interfaces and contracts), `configuration`, `failure_handling` and `required_behaviour`. Claude Code (`/harness`) drafts recommendations from the investigation report and the interview summary, and relays the developer's verdicts. The CLI only stores and renders; it never writes design content itself.

Provenance rules: a design entry exists only if the developer stated it (`add`), or accepted (or modified) a recommendation (`propose` then `decide`). Pending, rejected and withdrawn entries never appear as design content. A section that does not apply needs the developer's reason (`none --reason`). `harness design coverage` lists every uncovered or unresolved item (a section with no entry, a recommendation awaiting a decision, a section whose recommendations were all rejected, a section marked not applicable that also has elements) and says whether it is blocking: it blocks until all eight sections are covered. Each entry is marked **fixed** by the design (the default) or `--delegated` to AL, for details such as class and method names or file placement. `harness design show` renders the design section as Markdown, with each line tagged fixed or delegated and its provenance, plus a "Delegated to AL" list. It is the section P1-08 will embed in the spec.

Every mutating command is one transaction that records an event and a `design` checkpoint, and fails with exit 1 outside the design phase (the error says how to get there). `show` and `coverage` are read-only and work in any phase. Nothing is written to the target repository.

### Test plan

`harness testplan` captures the test scenarios of a task once it is in the `test_plan` phase (SPEC §3, Phase 5). A scenario belongs to one of six categories: `unit`, `integration`, `e2e`, `regression`, `edge_case` and `failure`. Claude Code (`/harness`) drafts only the scenarios that are relevant to the task, from the interview summary, the design and the investigation, and relays the developer's verdicts. The CLI only stores and renders; it never writes scenario text itself. A category with no scenario is reported but never blocks.

Provenance rules: a scenario is test-plan content only if the developer stated it (`add`), or accepted (or modified) a recommendation (`propose` then `decide`). Pending, rejected and withdrawn scenarios never appear as plan content. The developer can `add`, `edit` and `withdraw` scenarios at any time while the task is in the phase. Relevance is traceability: a recommendation must name at least one `--covers` reference, which is either an answered requirement `Q<n>` (an interview question with an answer) or an effective design entry `E<n>` of the same task. References are case-sensitive (`Q1`, not `q1`). A scenario the developer adds may omit `--covers`; the plan then shows `covers (not stated)`.

A mock is allowed only with a written justification: `--mock <target>` and `--mock-reason <why>` are required together (a scenario can hold both or neither, enforced in the database too). `harness testplan flag <id> <sid> --reason R` marks a mock that would stand in for behaviour the task must implement; a flagged scenario blocks approval until the flag is cleared with `flag --clear --reason R` (the reason is recorded), or the scenario is edited with a different mock, edited with `--no-mock` or withdrawn.

`harness testplan review` lists what is open: scenarios `awaiting_decision`, mocks `mock_flagged`, and the plan itself as `not_approved` or `changed_since_approval`, with `blocking: yes|no`. `harness testplan approve <id>` is the developer's plan-level approval, allowed only in the `test_plan` phase. It is refused while any scenario is pending or flagged, or when there are no scenarios. It records a content hash over the effective scenarios. The specification embeds the plan in its Test plan section only while that hash still matches the current scenarios and nothing is flagged: any later add, edit, withdraw, decision or flag (even a flag raised after approval and cleared afterwards) shows the placeholder again until a new `testplan approve`. Sealed `v<N>.md` specifications are never rewritten.

Every mutating command is one transaction that records an event and a `test_plan` checkpoint, and fails with exit 1 outside the test_plan phase (the error says how to get there). `show` and `review` are read-only and work in any phase. Nothing is written to the target repository.

### Decisions

`harness decision` manages the decision registry (SPEC §6.4, §6.3): one Git-versioned Markdown record per decision in the target repository's `docs/decisions` (by default the repository the task was investigated in, or `--decisions PATH`). A record carries its id, decision, rationale, scope, status, version, timestamp, supersedes and impact. A decision is recorded automatically when the developer approves it: `harness interview decide <id> <pid> accept|modify` on a decision question writes the record (the recommendation, or the developer's modification, with the rationale from the proposal and the `--impact` given to `interview propose`) and prints `recorded DEC-NNNN v1 -> <path>`. Rejecting writes nothing, and neither does a plain question. A decision the developer states directly is recorded with `harness decision record`. Blank scope and impact are stored as "not stated", never invented.

Changing an earlier decision never edits it in place. `harness decision change` writes nothing: it prints the original decision, rationale and impact, what references it, the proposed new version and the consequences of the change, and keeps the change pending. Only the developer's `harness decision decide <id> <cid> accept|reject|modify` writes a file, and it is refused if the record changed on disk in the meantime. The new version keeps the same ID with the version incremented and a `Supersedes` line. The previous version moves to `docs/decisions/history/DEC-NNNN-v<N>-<slug>.md` with status `superseded` and a `Superseded by` line, so the whole chain stays in Git and a fresh clone reads every approved decision. `harness decision list` and `show` display the latest version by default; `show --history` and `--json` expose the chain. Only approved decisions can be changed.

The three commands that write into the target repository's `docs/decisions` are `interview decide` (accept or modify on a decision question), `decision record` and `decision decide` (accept or modify on a pending change). `harness decision suggest --from PATH` is read-only: it reads approved decisions from the repositories you name with `--from`, never any others, and prints relevant ones as suggestions only. Nothing is written or applied, and adopting one means proposing it in the interview for the developer to decide.

### Specification

`harness spec` manages the task's implementation specification (SPEC §5, AC 30): a Markdown file in the target repository's `docs/specs/<task-id>/` (by default the repository the task was investigated in, or `--specs PATH`). The template has nine sections: Requirements, Architecture, Components and interfaces, Configuration, Acceptance criteria, Test plan, Rationale, Constraints and Unresolved items. They are rendered only from state the developer already stated or approved (the interview summary and the design section), with provenance; nothing is invented. The Test plan section embeds the plan approved with `harness testplan approve` (grouped by category, with provenance, mocks and their justifications) as long as the approval still matches the current scenarios; otherwise it reads `- (no approved test plan recorded)`.

A specification moves through three states: **draft**, **approved** and **superseded**.

- `harness spec show <id>` prints the preview and writes nothing.
- `harness spec draft <id>` writes the preview to `docs/specs/<id>/draft.md`. It is a working copy, not an approval.
- `harness spec approve <id>` is allowed only once the task is in the `approval` phase (otherwise it exits 1 and points to `harness phase <id> next`). It writes the immutable `v<N>.md` with its version, the repository `Revision` (the commit hash at HEAD), the `Working tree` state and a `Content hash: sha256:...` over the body, then removes `draft.md`. `Working tree` is an honest count from `git status --porcelain`: an untracked `draft.md` or earlier `v<N>.md` counts toward "N uncommitted paths", so commit your changes first if you want a clean tie to the revision. Without a commit at HEAD the approval is refused ("cannot be tied to a commit").
- Approving a new version marks the previous one `superseded`: only its header changes (status and a `Superseded by` line), the body and content hash are untouched. Approved files are never edited otherwise.
- `harness spec verify <id>` re-reads every file and prints one result per version: `ok`, `MODIFIED (...)` (content hash mismatch, a difference from the local approval record, a version that does not match the file name, an unparseable or missing file) or `draft (not sealed)`. It exits 1 on any `MODIFIED`. `harness spec list <id>` (with `--json`) lists the versions. Both work without a local database, at file level only.

`show`, `list` and `verify` never write. `draft` writes only `draft.md`, and `approve` writes only the new `v<N>.md` (plus the superseded header of the previous one). Task ids are validated before they are used as a path. `spec approve` also re-runs the final design review (see below) and refuses to seal while anything blocks. `spec approve` alone authorizes nothing: a sealed specification without an approval record cannot be handed off (see Approve & Implement below).

### Final review

`harness review <id> [--json]` is the mandatory completeness check of SPEC §3, step 6 (AC 10). It is read-only: it reads the state the earlier phases already recorded, never answers, decides or changes a phase, and exits 1 when anything blocks. The report always lists the ten items in SPEC order, each as `ok`, `BLOCKED` (with its findings and a `→` hint) or `n/a`, followed by `blocking: yes|no` and, when a phase can fix it, a `route back:` line. What each item reads and where it routes:

| Item | Blocked when | Route |
| --- | --- | --- |
| Requirements are clear | interview checklist items `not_asked` | interview |
| Significant decisions are approved | a recommendation `awaiting_decision` in the interview, design or test plan | earliest of interview, design, test_plan |
| Architecture is consistent | a conflicting design entry, or configuration, failure_handling or required_behaviour uncovered or rejected | design |
| Component responsibilities are defined | components or responsibilities uncovered or rejected | design |
| Component interactions are understood | interactions, data_flow or interfaces uncovered or rejected | design |
| Acceptance criteria are testable | no `acceptance_criteria` requirement recorded (the developer judges scenario coverage when approving the test plan) | interview |
| The test plan is sufficient | plan `not_approved` or `changed_since_approval`, or a scenario `mock_flagged` | test_plan |
| No blocking questions remain | interview questions `unanswered` or with a rejected recommendation | interview |
| No unresolved diagram comments remain | never: reported `n/a` ("diagrams and comments are Phase 2") | none |
| No known conflicts are ignored | a proposed decision change still `pending` | none: `harness decision decide <id> <cid> accept\|reject\|modify` |

The route-back hints (`harness phase <id> back <phase> --reason "<why>"`) are advice only: the review never moves the task. The developer decides whether to go back.

The review is a gate in both places a specification can advance. `harness phase <id> next` from `final_review` into `approval` runs it and, while anything blocks, exits 1 with the report and writes nothing. When it passes, the transition records a `final_review.passed` event and a `final_review` checkpoint (state hash and per-item states) in the same transaction, and prints `final design review passed (checkpoint recorded)`. `harness spec approve <id>` runs the review again (a decision change proposed while in `approval`, for instance, blocks it) and seals nothing while it blocks. A specification with an open blocking item therefore cannot reach approval.

### Resume

`harness resume [<id>] [--json] [--specs PATH]` answers "where does an interrupted task continue?" (SPEC AC 12). It is derived from the committed SQLite state only, and it writes nothing: no row, event, checkpoint or file, and it never creates the database. Every mutating command is one transaction, so a process killed mid-way (for example in the middle of an interview) loses only its uncommitted transaction and the committed rows are the last consistent checkpoint. Resuming the interview therefore continues from the same question.

`harness resume <id>` prints:

- `task <id> · phase <phase> · complexity <level> (<source>)`
- `request: <the original request>`
- `checkpoint: <label> <timestamp> (#<n>)` for the latest checkpoint recorded in the task's current phase, or `checkpoint: none in this phase`
- in the interview phase, the pending question exactly as `harness interview next` prints it (`Q<n> [<topic>] <text>`, with any pending recommendation or `recommendation needed` hint)
- `approval: <status>` in the `approval` phase only (the same status `harness show` prints)
- one `note:` line per finding about the draft (see below)
- `next: <command>`, the command to run to continue, or `nothing to resume: approved for implementation (handoff is Phase 3)`

The `next:` command depends on the phase:

| Phase | `next:` |
| --- | --- |
| any phase, no `investigation` checkpoint yet | `harness investigate <id>` |
| `investigation` | `harness phase <id> next` |
| `interview` | `harness interview next <id>` when a catalogue re-sync is due (no question is shown), or when a question is pending (that question is printed); otherwise `harness interview checklist <id>` while the checklist blocks; otherwise `harness phase <id> next` |
| `design` | `harness design coverage <id>` while coverage blocks, otherwise `harness phase <id> next` |
| `test_plan` | `harness testplan review <id>` while the review blocks, otherwise `harness phase <id> next` |
| `final_review` | `harness review <id>` while the final review blocks, otherwise `harness phase <id> next` |
| `approval` | `nothing to resume: approved for implementation (handoff is Phase 3)` when the approval is current; otherwise `harness review <id>` while the final review blocks, otherwise `harness approve <id>` |

`harness resume` without an id lists the resumable tasks: every task except those approved with a current approval, most recently updated first. Each task is three lines: `<id> · phase <phase> · complexity <level> · updated <timestamp>`, then `  <request>`, then `  next: <command>`. With no database it prints `no tasks to resume` (`[]` with `--json`) and creates nothing. `--json` prints the resume point (or the list) as JSON.

`draft.md` is the only file the harness writes inside a transaction. When `docs/specs/<id>/draft.md` exists but no committed `spec` checkpoint records it (the write was interrupted), the output adds `note: unrecorded draft <path> (the write was interrupted); harness spec draft <id> rewrites it`. Resume never rewrites it for you.

Exit codes: `0` on success (including an empty list), `1` for an unknown task id (`harness resume: unknown task: <id>`).

### Dashboard

`uv run python -m harness.dashboard [<id>] [--db PATH] [--specs PATH] [--no-browser]` starts the local web dashboard (Phase 2, P2-01 to P2-07). `/harness` starts it for the active task and opens it in the browser. It prints `dashboard: http://127.0.0.1:<port>/ · task <id> · Ctrl-C stops it` (`no active task` when no id is given), then serves until Ctrl-C or SIGTERM, which print `dashboard stopped` and exit 0. If no browser can be opened it also prints `could not open a browser; open <url> yourself`; `--no-browser` only prints the URL. If the server cannot bind, it prints `harness dashboard: <error>` on stderr and exits 1.

It is a standard-library HTTP server (`http.server`) with a static HTML, CSS and vanilla JavaScript frontend: no build step and no new dependency (E9). It is not a `harness` subcommand, so the CLI and its output are unchanged (DEC-0006, DEC-0012).

- **Local only**: it binds to `127.0.0.1` on a free port chosen by the OS on every run; there is no option to change the host. A request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` gets 403 (protection against DNS rebinding). There is no login (DEC-0016).
- **Same state as the CLI**: the database and the specification directory are resolved exactly like the CLI (`--db` / `$HARNESS_DB` / the per-repository default, and `docs/specs/<id>/` or `--specs`). The browser never touches SQLite: the server reads through the same `harness.db` connection and controller on every request, so a change made with the CLI is pushed to an open page within about a second by the live stream (no reload).
- **What it shows**: the task list and, for the selected task, the overview (SPEC §8.1, §8.9). Every value comes from the engines the CLI uses, so it matches the CLI's own output:

  | Field | Source |
  |---|---|
  | Task | the task id (`Task <id>`) |
  | Description | the task's request |
  | Phase | the task's current phase |
  | Status | `approved` (approved with a current approval, nothing to resume), else `awaiting approval` (something is pending below), else `awaiting answer` (an interview question is open), else `in progress` |
  | Elapsed | now minus the task's creation time, ticked every second in the browser (`45s`, `12m 05s`, `3h 04m`, `2d 03h`) |
  | Complexity, Specification, Spec directory | the task's complexity with its source, the specification versions with their status (and whether a draft exists), and the specification directory |
  | Pending approvals | what waits on the developer, each with the CLI command that decides it: interview recommendations (`harness interview decide <id> <pid> accept\|reject\|modify`), design recommendations (`harness design decide ...`), test scenarios (`harness testplan decide ...`), decision changes (`harness decision decide ...`), the test plan in the `test_plan` phase while it is not approved (`harness testplan approve <id>`) and the final approval in the `approval` phase while there is no current approval (`harness approve <id>`). They are listed for review only: deciding stays with the CLI (the page shows them in the Approval Center, see below) |
  | Latest activity | the newest recorded event of the task (kind and time), or `none` |
  | Resume | the resume point, the same as `harness resume <id> --json`: the next command (or `nothing to resume: approved for implementation (handoff is Phase 3)`), the pending question, the checkpoint, the approval status and notes |

  No "overall progress" is shown: the specification (§8.3) forbids a fabricated percentage, and the overview does not compute one.
- **Tasks and switching**: the left-hand list shows every task, most recently updated first, with its phase, status, request and the command to continue it (the same `next` as `harness resume`). Selecting one navigates to `/?task=<id>`; the page then asks the server for that task, and the link of the selected task is marked as current. Without `?task=` the dashboard shows the task it was launched for. An empty `?task=` behaves like no `?task=`; an unknown or malformed `?task=` shows `unknown task: <id>` with the full list, never an error. A task whose stored state cannot be read is listed with the status `unavailable: <reason>` and does not break the list. Resuming an interrupted task means showing where it continues (the Resume section and the `next` in each row); continuing it is still done with that CLI command or `/harness`.
- **Specification** (P2-03, SPEC §5 Specification Storage): the selected task's current spec, rendered as formatted text. The spec shown is the highest approved `v<N>.md`, else `draft.md`, else the highest version file; the page says which one (`showing: v2 (approved)`, `showing: draft (draft)`) and its file path. A task with no spec file shows `not drafted yet` and `phase: <phase>`. The Markdown is rendered from the small subset the harness itself writes (headings, bullet items with continuation lines, bold field labels, inline code) by a standard-library parser on the server; the page builds DOM nodes with `textContent` only (no `innerHTML`, no Markdown library, no new dependency), so anything else in the text, including HTML, is shown literally and never interpreted. If the shown file cannot be read or `draft.md` is malformed, the section says `specification unavailable: <reason>` and the rest of the page still renders; a malformed `v<N>.md` still fails the whole view as before (500 on `/api/dashboard`, a `failure` event on the stream). JSON: `task.spec.shown` (label, status, version, path, parsed blocks), `task.spec.unavailable`.
- **Version history** (P2-03, ROADMAP Phase 2, SPEC §5 Specification Storage): every spec version in ascending order, the draft last, each as `<label> <status> · <timestamp> · revision <rev>` (with `superseded by v<M>` when it was) and the same fields as `harness spec list --json`. Each version has a collapsible diff of its body against the previous existing version file (the first one says `first version`); the draft is compared against the highest version file (`no version to compare against` when there is none). Diffs cover the body only, which is what the content hash seals, and show the changed lines with three lines of context, `+`/`-` markers and a `+A -R` count; identical bodies show `no changes to the body`. An open diff stays open across live updates. JSON: `task.spec.history` (entries with `label` and `diff`).
- **Decisions** (P2-03, SPEC §6.4 Decision Versioning): two separate lists under the registry directory, so approved and open decisions are never mixed. *Approved decisions* are all current approved records of the task's decision registry (the investigated repository's `docs/decisions`; a task that has not investigated a repository yet uses the current directory's git top-level, as the CLI does), each with id, version, title, scope, timestamp, decision text, file and `recorded by this task` when the task's own events recorded it; superseded, draft and rejected records are not listed. *Open decisions* are what the engines still wait on the developer for: interview checklist items awaiting a decision or of the decision kind (including catalogue decision questions that were never asked, state `not_asked`), pending design entries, pending test scenarios and pending decision changes, each with its state and, where one can be decided, the CLI command (`harness interview|design|testplan|decision decide ...`, the same as in Pending approvals). Nothing is invented: both lists come from the existing engines and the registry. A malformed decision record marks the approved list `decisions unavailable: <reason>` and the rest still renders. JSON: `task.decisions.directory`, `.approved`, `.unavailable`, `.open`.
- **Diagrams** (P2-04, SPEC §13, DEC-0007, DEC-0009, DEC-0015): the Diagram Service turns the selected task's design into diagrams and keeps every version. It is read-only for the developer: diagrams are generated from the design, never edited directly. The model comes only from *notation lines* in effective design entries (stated by the developer, or recommendations the developer accepted or modified; a modified one counts with the developer's response). A notation line starts, after an optional `- ` or `* ` bullet, with one of six lowercase keywords. Every other line is prose: it stays in the spec and does not change the diagrams. Nothing is inferred from prose and nothing is invented; each element lists the design entries (`E<n>`) that stated it.

  | Notation line | Adds | Diagram |
  |---|---|---|
  | `component <name>[: <description>]` | a declared component | component |
  | `connect <from> -> <to>[: <label>]` | a connection between two components | component |
  | `message <from> -> <to>: <text>` | one message (the text is required) | sequence |
  | `flow <from> -> <to>[: <data>]` | a data flow between two components | data-flow |
  | `state <machine>: <from> -> <to>[: <event>]` | a transition of a state machine | state |
  | `deploy <component> @ <node>` | a component placed on a node | deployment |

  Only the **relevant** diagrams are produced: the component diagram when the model has a declared component or a connection, and each of the others only when it has at least one message, flow, state machine or deployment. Components named only as an endpoint (for example in `connect`) appear as undeclared components. A design with no notation shows `no diagrams yet`, plus a hint on how to add notation when it has design entries but none of them is notation (the hint says "stated or accepted" entries; a recommendation the developer modified counts too, as above). There are no size limits: a large design scrolls inside the diagram area and every element stays selectable. Edges between boxes in different layers are routed around the boxes in between; dense graphs may still have crossing arrows or labels.

  Every element has a **stable id**, derived from its names alone (never from database ids, time or entry order): `component:<slug>`, `connection:<from>-><to>`, `flow:<from>-><to>`, `message:<from>-><to>:<k>`, `machine:<m>`, `state:<m>/<s>`, `transition:<m>/<from>-><to>`, `node:<slug>` and `deployment:<component>@<node>`. A slug is the name lowercased (case-folded) with every run of non-word characters turned into `-`, so `API_Gateway`, `api gateway` and `API-Gateway` are the same component with one id (its label is the first spelling used, unless a `component` line declares another). Decomposed and precomposed Unicode spellings of the same text may get different ids. `<k>` in a message id is the 1-based ordinal of the message for that sender and receiver pair, in the order the lines are stated. Repeating a line merges into the same element, with every source kept.

  A notation line that starts with a keyword but does not fit its form (a missing `->`, a missing message text, an empty name, a name containing `->`) is never guessed at: it contributes nothing and is listed as a notation issue (`E<n> line <n>: <keyword>: expected <form>` with the line). The same applies to a lowercase prose line that happens to start with a keyword and does not fit: it is listed as an issue and does not change the diagrams.

  **Versions**: a version is an immutable snapshot of the whole model (every relevant diagram), numbered 1, 2, 3 and so on per task, with a content hash. The dashboard records a new version when it renders a view of the task and the model differs from the latest version (or there is no version yet and the model is not empty); a design that loses all its notation records an empty version once versions exist. The CLI does not record versions: a design change made with the CLI becomes a version the next time the dashboard shows the task (the stream does so within about a second), and the page says so while a change is not recorded yet. Versions are only appended, never changed. Old versions keep elements the design has since removed. If the version cannot be recorded (for example the database is locked), the section says `diagrams unavailable: <reason>`, the rest of the page still renders, and the next view tries again. The only table written is `diagram_versions` (migration `0009`): no event, checkpoint or task timestamp changes, a task without notation writes nothing, and a missing database is never created.

  **Browsing**: the page shows the current version and its history, and any stored version can be opened (`showing: version N (not current)`, with `show current` to go back). Two versions can be compared side by side (see the redesign loop under Comments). Each element is selectable by click, Enter or Space; the selection shows its id, kind, label, version and sources. The URL hash `#version=N&element=<encoded id>` (`#element=<encoded id>` for the current version) selects an element and survives live updates, and it is the anchor comments use (element id plus diagram version). JSON: `task.diagrams` (`current`, `history`, `issues`, `notice`, `hint`, `outdated`, `unavailable`), and `GET /api/diagrams?version=N[&task=<id>]` returns one stored version (`GET /api/diagrams/compare` compares two, see the redesign loop). It answers 400 when `version` is missing or not a positive integer, and 404 for an unknown version, an unknown task, no active task or a version too large to exist. The SVG is built by `createElementNS` and `textContent` only (no `innerHTML`, no library, no new dependency).
- **Comments** (P2-05, SPEC §3 step 4, §8.6, DEC-0008, DEC-0011, DEC-0014): the developer comments on a diagram element, and the comment is stored in the task's SQLite state tied to the element id and the diagram version.

  - **Adding**: select a component or connection (any addressable element of the current diagram version). Below its details, "Comments on this element in version N" lists that element's comments and a form takes a **kind** (`clarification`, `correction` or `redesign`) and the comment text, then **Add comment**. There is no length limit. The text is stored trimmed of leading and trailing whitespace and otherwise unchanged, and shown literally (`textContent` only, so HTML is never interpreted). Only the current version of an up-to-date design has the form; an older version, a design changed since the page loaded, or unavailable diagrams show a note instead (`comments can be added only on the current version`, `the design changed; the new diagram version appears after a reload`).
  - **Storage** (migration `0010`, table `comments`): task, diagram version, element id, kind, text, status, request id and timestamps. The row refers to an existing stored diagram version of the task by a foreign key, and the element id must exist in that version. Rows are only appended; only the status and `updated_at` ever change.
  - **States**: `open` → `addressed` → `resolved`. An addressed or resolved comment can be re-opened. The engine refuses every other move (an open comment cannot be resolved directly), and a move to the state a comment already has changes nothing and records nothing. A new comment is `open`. Only the harness addresses a comment (`python -m harness.redesign address`, see the redesign loop) and only the developer resolves or re-opens one (the buttons in the page, over `POST /api/comments/resolve` and `/reopen`). The dashboard has no route and no button for address: neither the HTTP API nor the page can address a comment.
  - **Stale refusal** (DEC-0008): a comment on a diagram version that is no longer current is refused with `409`, `reload: true` and a hint, and so is one made while the design has changed since that version and the dashboard has not recorded a new version yet (the check and the write are one transaction, and nothing is recorded). The page then keeps the typed draft, shows the refusal and offers **Reload**, which re-reads the dashboard data (the new version appears and the comment can be made there).
  - **Retry** (DEC-0014): every submit carries a `request_id`. When the connection fails or the database is busy (`503`) the page retries up to 3 attempts in all, waiting 500 ms and then 1 s, and then shows **Retry**, which sends the same id again. A repeated id with the same content answers `200` with the comment already stored (no duplicate); the same id with different content is refused with `409`.
  - **Page**: below the diagrams, a `Comments (open N · addressed N · resolved N)` section lists every comment of the task: `#<id> · <status> · <kind> · <element kind> <label> (<element id>) · version N`, the text and a link that opens the element (in its own version when that is not the current one). The element's label and kind are taken from the comment's own version. A comment whose element the design has since removed stays listed, marked `(not current)` when its version is not the current one, and its link opens the old version. A draft and the focus in the form survive live updates, and a draft carries over to a new current version when the element still exists there.
  - **Events and JSON**: every write records one event in the same transaction: `comment.added` (`id`, `version`, `element`, `kind`; the text is not copied) and, for engine transitions, `comment.addressed`, `comment.resolved` and `comment.reopened` (`id`, `from`, `to`; a `comment.addressed` recorded by the redesign tool also carries `version`, the diagram version it was addressed in). Comment events show as the task's latest activity. No checkpoint is written and the task's timestamp does not change. JSON: `task.comments` (`kinds`, `counts` with `open`, `addressed` and `resolved`, `items` with `id`, `version`, `element`, `element_kind`, `element_label`, `kind`, `text`, `status`, `created_at` and `updated_at`, oldest first, and `addressed_in`: for each comment that is addressed or resolved, the version in its latest `comment.addressed` event that carries one).
  - **Redesign loop** (P2-06, SPEC §3 step 4, §8.6, Q25, amendment A1): the harness takes the open comments, the design is updated through the existing commands, the diagrams are regenerated and the developer closes each comment. The tool is `python -m harness.redesign`, a module entry point like the dashboard and not a `harness` subcommand, so the CLI's commands, output and `--help` are unchanged (DEC-0006, DEC-0012). `/harness` runs it (see `.claude/commands/harness.md`).
    1. **List**: `uv run python -m harness.redesign <task id>` lists the open comments, oldest first, and writes nothing (it does not even record diagram versions). The first line is `task <id> · diagram version <N> (current) · comments: open a · addressed b · resolved c` (`no diagrams yet` before the first version). When the design changed since the latest version it adds `the design changed since diagram version N; address records the new version`. Each open comment is `#<id> · <kind> · <element kind> <label> (<element id>) · version <v>` (`(not current)` is appended when `v` is not the latest version; the element's kind and label come from the comment's own version), followed by its text, every line indented by four spaces and otherwise verbatim. The output ends with `address: uv run python -m harness.redesign address <id> <comment id>...`, or with `no open comments`.
    2. **Change the design**: only through the existing CLI (`harness phase <id> back design --reason ...` when the task is past design, then `design propose`, `design decide` and `design withdraw`). The developer decides; the tool never edits the design. A changed design becomes a new immutable diagram version.
    3. **Address**: `uv run python -m harness.redesign address <task id> <comment id>...` first runs the Diagram Service sync (the same idempotent write the dashboard does; it prints `recorded diagram version N` when it recorded one), then moves every named comment `open` → `addressed` in one transaction, each with a `comment.addressed` event carrying the version. It prints `#<id> addressed in diagram version N` per comment, with `(diagrams unchanged since version N)` added when the comment's own version is already the latest (a clarification answered in prose changes no diagram), and `#<id> already addressed` for a comment that was (a rerun is safe and records nothing). The batch is all or nothing: an unknown id (including another task's comment) or a resolved comment (only an open one can be addressed) refuses it and writes no comment and no event (a sync row that was recorded stays), and so does a design that changed between the sync and the write (`the design changed while addressing; run address again`). Duplicate ids count once. Errors print `harness redesign: <error>` on stderr and exit 1 (`; retry` is appended when the database is busy); a missing database is never created (`unknown task: <id>`), and a malformed argument exits 2. Both commands take `--db PATH` (default `$HARNESS_DB` or the per-repository database, like the CLI). Write surface of `address`: at most one `diagram_versions` row, then the `comments` status changes and their events. No checkpoint, no file and no task timestamp.
    4. **Resolve and re-open** are the developer's, in the page: an addressed comment shows `addressed in version N` with **Resolve** and **Re-open**, a resolved one shows **Re-open**, and an open one shows only the note `open: waiting for the harness to address it`. While a request is in flight the comment's buttons are disabled and show `saving…`; a failure that can be retried (network error, `503`) shows the error with **Retry**, and a refusal (`409`, `404`) shows the error and reloads the data. `POST /api/comments/resolve` and `POST /api/comments/reopen` take a JSON body with exactly `task` (non-empty string) and `comment` (a positive integer, not a boolean) and no request id: a move is idempotent by target state, so a move to the state the comment already has answers `200` `{"comment": {...}, "changed": false}` and writes nothing, and a real move answers `200` `{"comment", "changed": true}` after writing the status change and its event (`comment.resolved` or `comment.reopened`) in one transaction. The other answers are `409` `{"error"}` for a move the state machine refuses (resolving an open comment; there is no `reload` flag), `404` for an unknown task or comment (another task's comment included; a missing database is never created), `400` for an invalid body, `503` `{"error", "retry": true}` when the database is busy and `500` for any other storage error. There is no stale-version check: a comment on an element the design has since removed can still be resolved and re-opened. The headers are gated in the same order as for `POST /api/comments`, and `POST /api/comments/address` does not exist (501).
    5. **Compare versions**: below the diagrams, the **Compare versions** section shows two stored versions side by side, by default the current one next to the one before it (`no earlier version to compare with` when there are fewer than two). The **From** and **To** selects pick any earlier and any later version; a picked pair stays while it is valid, and an unpicked pair follows the newest version. A comment addressed in a later version than its own has a `compare version v → N` button that picks that pair. Both panes render every diagram of their version (a diagram type that exists on one side only still renders); the earlier pane is titled `(previous)` and the later one `(current)` or `(not current)`. A click on an element in a pane opens it in the Diagrams section. Highlights use text and pattern as well as colour: added is a solid outline, removed dashed and changed dotted, with a legend and a list `removed|changed|added: <kind> <label> (<id>)` whose entries link to the element in its version (`no diagram changes between version N and M` when nothing differs). On narrow windows (`max-width: 48rem`) the panes stack; on wide ones they sit in two columns, and each pane scrolls horizontally rather than shrinking, so a large design stays fully rendered.
       **What counts as a change**: elements are matched by their stable id and compared on what the diagrams show: components (name, declared, descriptions), connections and flows (from, to, labels), state machines and states (name), transitions (from, to, events), nodes (name) and deployments (component, node). The design entries that stated an element (`sources`) never count, so withdrawing an entry and stating the same notation again shows no change; an undeclared component that becomes declared is changed. Messages are matched as a multiset by `(from, to, text)`, pairing occurrences in order, because a message id carries an ordinal that shifts when an earlier message between the same pair is added or removed. A message is therefore only ever added or removed, never changed (a changed text shows the old message removed and the new one added), and a message whose ordinal merely shifted is not highlighted. Caveat: when identical messages between the same pair are removed, the later occurrences are the ones shown as removed, not necessarily the ones the developer deleted.
       `GET /api/diagrams/compare?from=N&to=M[&task=<id>]` returns `{"from", "to", "changes", "marks", "counts"}`: `from` and `to` are the version metadata of `GET /api/diagrams` with their `diagrams`; `changes` is a list of `{"id", "kind", "label", "change"}` (removed, then changed, then added, each by id; a removed element's label comes from the earlier version); `marks` is `{"from": {id: change}, "to": {id: change}}` (side-specific: removed and changed on `from`, added and changed on `to`); `counts` has `added`, `removed` and `changed`. It answers 400 when `from` or `to` is missing or not a positive integer, or when `from` is not smaller than `to`, and 404 for an unknown version, an unknown task, no active task or a version too large to exist. It is read-only: it records no diagram version, writes nothing and never creates the database.
- **Approval Center** (P2-07, SPEC §8.7, SPEC §3 Phase 7, DEC-0006, DEC-0007, DEC-0008, DEC-0014, DEC-0016): one section of the page, `#approvals`, replaces the overview's flat pending list and shows every pending approval in three groups: *Decisions* (interview recommendations, design recommendations and decision changes), *Test plan* (test scenarios and the test plan) and *Final spec* (the final approval). The items are the overview's `pending approvals` split by kind, so there is no second source: each shows `<ref> · <kind>: <title>` and the CLI command that decides it (`decide: ...`), or the group says `none`. Decisions and the test plan are decided only with the CLI (the dashboard may not write them, DEC-0007). The Final spec group shows `approval: <status>` when there is one, the **Approve & Implement** button, the reasons it is disabled, the CLI alternative (`or run: harness approve <id>`) and, in the `approval` phase, the specification that the button will seal (a collapsible `Specification to be sealed (sha256:...)`, rendered like the Specification section, with `textContent` only).

  The button is enabled only when nothing blocks. Each blocker is a line with a `data-code`, in this order (the text is what the page and the JSON show):

  | Code | Shown when | Text |
  |---|---|---|
  | `phase` | the task is not in the `approval` phase | `the task is in phase <phase>; Approve & Implement opens in the approval phase` |
  | `approved` | the approval status ends with `(current)` | `already approved: <status>` |
  | `stale` | the design changed and its new diagram version is not recorded yet | `the design changed and its new diagram version is not recorded yet; reload` |
  | `comments` | any diagram comment is not `resolved`, so `open` and `addressed` both block | `<n> diagram comment(s) not resolved (open <a> · addressed <b>)` |
  | `questions` | the final review's blocking-questions check blocks | `blocking questions remain: <findings>` |
  | `review` | the final design review blocks (the same check as `harness review <id>`) | `the final design review fails (harness review <id>): <blocked items>` |
  | `unavailable` | the approval state cannot be read (for example a malformed decision file); the rest of the page still renders | `approval state unavailable: <reason>` |

  With no blocker the page says `ready to approve: every comment is resolved, no blocking question remains and the final review passes`. The state is live: resolving the last comment enables the button within about a second, without a reload. While a request is in flight the button is disabled and the page says `approving…`; success shows the CLI's output lines (`approval #<n> recorded: ...`) and the `approved` blocker. A failure that can be retried (network error, `503`) is retried up to three times (500 ms, then 1 s) and then shows the error with **Retry**, which sends the same request again and never records a second approval. A stale refusal shows the error and **Reload**. The page keeps the in-flight, error and Retry state across live updates. JSON: `task.approval_center` with `groups` (`decisions`, `test_plan`, `final_spec`, each a list of the same items as `task.pending_approvals`) and `final` (`phase`, `status`, `command`, `version`, `review_hash`, `spec_hash`, `preview`, `comments` as `{open, addressed}`, `blockers` as a list of `{code, text}` and `enabled`). `task.pending_approvals` is unchanged. `spec_hash` and `preview` are set only in the `approval` phase.
- **Routes**: `GET /api/dashboard[?task=<id>]` returns the read model as JSON, `GET /api/diagrams?version=<n>[&task=<id>]` returns one stored diagram version (see Diagrams), `GET /api/diagrams/compare?from=<n>&to=<m>[&task=<id>]` compares two (see the redesign loop), `POST /api/comments` adds a comment (see below), `POST /api/comments/resolve` and `POST /api/comments/reopen` move one (see the redesign loop), `POST /api/approve` records the approval (see Approval Center and below) and `GET /api/stream[?task=<id>]` is a server-sent-events stream of the same JSON (an `EventSource` in the page). The stream polls the state about once a second and sends a frame only when the view changed, so a CLI change, a new spec file or a recorded decision shows up without a reload. It announces `retry: 2000`, sends a `: keep-alive` comment after 15 seconds without a change and is recycled by the server after 300 seconds (the browser reconnects). If the data cannot be read, it sends a `failure` event with the error and keeps polling, so it recovers once the problem is fixed. Every response, including 403, 404, 500 and the stream, carries the security headers (content security policy, `nosniff`, no cache, no referrer).

  `POST /api/comments` adds a comment; the resolve, re-open and approve routes share its gates and are described in the redesign loop and below. The body is one JSON object with exactly the keys `task`, `version` (a positive integer), `element`, `kind` (`clarification`, `correction` or `redesign`), `text` (not empty after trimming; no length limit) and `request_id` (8 to 64 letters, digits or hyphens). The headers are checked in this order, before any body byte is read: an unexpected `Host` gets 403; a path other than `/api/comments`, `/api/comments/resolve`, `/api/comments/reopen` or `/api/approve` gets 501; an `Origin` that is not `http://127.0.0.1:<port>` or `http://localhost:<port>` gets 403 (a request with no `Origin`, such as plain `curl`, is refused too, so a page on another site cannot post to the dashboard); a `Sec-Fetch-Site` other than `same-origin` gets 403 (an absent `Sec-Fetch-Site` is accepted when the `Origin` is valid); a content type other than `application/json` gets 415; a `Transfer-Encoding` or a missing `Content-Length` gets 411; a repeated, non-numeric or more than 18 digit `Content-Length` gets 400. A body that is shorter than its length, not UTF-8 JSON, not an object, has duplicate or unknown or missing keys, or has an invalid value gets 400. Then:

  | Status | Meaning |
  |---|---|
  | `201` | the comment was created: `{"comment": {...}}` |
  | `200` | a replay of the same `request_id` with the same content: the stored comment |
  | `409` | stale: `{"error", "reload": true}`; or the `request_id` was used with different content: `{"error"}` |
  | `404` | unknown task, diagram version or element (a missing database is never created) |
  | `400` | invalid body |
  | `503` | the state database is busy: `{"error", "retry": true}`; nothing was written |
  | `500` | any other storage error |

  `POST /api/approve` (Approve & Implement) takes a JSON body with exactly the keys `task` (non-empty string), `version` (the diagram version the page showed: `null` or a positive integer, not a boolean), `spec_hash` and `review_hash` (each `sha256:` followed by 64 lowercase hex digits): the tokens of the rendered `task.approval_center.final`. It runs the same approval as `harness approve` (see Approve & Implement) and checks everything inside that one transaction, before anything is written. Status codes:

  | Status | Meaning |
  |---|---|
  | `201` | approved: `{"created": true, "approval": {...}, "spec": {"version", "path"}, "superseded", "output": [...]}`; `output` is the lines `harness approve` prints |
  | `200` | the approval was already current: `{"created": false, "approval", "output"}`; nothing was written (a replay, a second click or a retry after success) |
  | `409` | stale: `{"error", "reload": true}` when the diagram version, the design, the final review or the specification changed since the page rendered; otherwise `{"error"}` for a diagram comment that is not resolved, the wrong phase, a final design review that blocks, or no commit at HEAD |
  | `404` | unknown task (a missing database is never created) |
  | `400` | invalid body |
  | `503` | the state database is busy: `{"error", "retry": true}`; nothing was written |
  | `500` | any other storage error; the engine rolled everything back and restored `draft.md` |

  Every other method (`PUT`, `OPTIONS` and the rest) answers 501, and every error carries the security headers.
- **Stale data**: the header shows `live` while the stream is connected. When the connection drops, or the data cannot be read, the page keeps the last loaded data, marks it as stale and says why. It then reconnects, waiting 2 seconds and doubling the wait up to 30 seconds, and goes back to `live` on the first successful connection.
- **Writes only diagram versions, comments and the approval snapshot**: P2-02 and P2-03 write nothing, from P2-04 the dashboard records diagram versions (a `diagram_versions` row, written by the Diagram Service when the design changed; see Diagrams) with no event, checkpoint or file, from P2-05 it also writes comments: only through `POST /api/comments`, and from P2-06 `POST /api/comments/resolve` and `/reopen`, which change only a comment's status (never to `addressed`), each as one transaction holding the `comments` row (or its status change) and its event (see Comments). From P2-07 it also writes the approval snapshot, only through `POST /api/approve`, and only what `harness approve` writes in the same transaction: the sealed `v<N>.md` (and the superseded header of the previous version, and the removal of `draft.md`), a `spec_versions` row, an `approvals` row, the `spec.approved` and `approval.granted` events and the `spec` and `approval` checkpoints (see Approve & Implement). A refused or failed approval writes nothing. Otherwise it writes no checkpoint, no file and no task timestamp, and it never creates the database: switching is navigation, the spec, decision, diagram and Approval Center views only read, and a GET or the stream writes nothing besides recording a changed design's diagrams; the comment routes never record a diagram version, and the compare endpoint only reads. Opening an existing database applies pending schema migrations exactly like the CLI does; a missing database is never created. DEC-0007 allows the dashboard to write only comments, diagram versions and the approval snapshot. The final acceptance is P2-08.

## Approve & Implement

`harness approve <id> [--specs PATH]` is the developer-facing "Approve & Implement" step (SPEC §3 Phase 7). It is allowed only in the `approval` phase and, in one transaction, seals a new `v<N>.md` exactly as `spec approve` does (same output, including the superseded line for the previous version) and records the approval. It also re-runs the final design review and writes nothing (no file, row, event or checkpoint) if anything blocks, the task is outside `approval`, or there is no commit at HEAD. Success also prints `approval #<n> recorded: spec v<N> <content hash> (implementation handoff authorized; AL launch is Phase 3)`. The harness does not launch AL yet: that is Phase 3.

**From the dashboard (P2-07).** The Approval Center's **Approve & Implement** button (`POST /api/approve`, see Dashboard) runs this same approval: it calls the same `ApprovalGate.approve` inside the same single transaction and prints the same lines (`approval #<n> recorded: ...`), so the sealed `v<N>.md`, the `spec_versions` and `approvals` rows, the `spec.approved` and `approval.granted` events and the `spec` and `approval` checkpoints are the ones `harness approve` writes. It adds checks that run inside that transaction, before anything is written. It refuses while any diagram comment is not resolved (`open` or `addressed`), while a blocking question remains and while the final design review fails. It refuses a page that is out of date: if the diagram version, the design, the final review or the specification changed after the page rendered, it answers `409` with `reload: true` and writes nothing (DEC-0008). It is safe to retry (DEC-0014): an approval that is still current is never sealed again, so a replay, a second click or two tabs get `200` `created: false` and exactly one approval is recorded. Unlike `harness approve`, which approves again and seals a new version, the dashboard never re-approves an approval that is still current. "& Implement" authorizes the handoff only, exactly as the CLI does; the harness does not launch AL (that is Phase 3).

**Limitation (A2, DEC-0006, Q16).** Only the dashboard enforces "every diagram comment is resolved". The CLI `harness approve` does not check diagram comments (`harness review` keeps reporting `No unresolved diagram comments remain` as `n/a`, so the CLI's behaviour, output and review state hash are unchanged), so it can approve while a comment is unresolved. An approval also stays current if a comment is re-opened after it: whether an approval is current never looks at comments. Use the dashboard button when comments matter.

An approval binds the specification version, the path of the sealed file, the git revision, the content hash and the final-review state hash. It is an immutable row in the local database (approving again adds a new row and seals a new version; earlier rows are never changed), plus an `approval.granted` event and an `approval` checkpoint. Whether an approval is still current is never stored: it is re-derived each time, and the approval is stale (with a reason) when any of these holds:

1. `no approval record; run: harness approve <id>`.
2. `the task is in phase <phase>, not approval; run: harness approve <id> again once it is back in approval`.
3. `the task left the approval phase after the approval was granted; run: harness approve <id> again` (re-entering `approval` does not revive it).
4. `spec v<N> is not recorded as approved; run: harness approve <id> again`, or `spec v<N> was superseded by v<M>; run: harness approve <id> again` (for example after a later `harness spec approve`).
5. `the sealed specification <path> cannot be read: <error>`, or `the sealed specification <path> was modified after approval (run: harness spec verify <id>)`.
6. `the specification content changed since approval; run: harness approve <id> again`.
7. `the final design review state changed since approval (e.g. a pending decision change); run: harness review <id>`.

`harness spec approve` alone authorizes nothing: it seals a version but records no approval, and it supersedes the approved version of an earlier `harness approve`. `harness.approval.require_approval(controller, task_id, store, repo_root)` is the only handoff path Phase 3 may use: it returns the current approval (spec path, version and hash) or raises `NotApproved` with the first reason above. `harness show <id>` prints an `approval:` line: `none`, `spec v<N> <hash> (current)` or `spec v<N> <hash> (stale: <reason>)`.

## Status

Phase 1 done (P1-01 to P1-13); Phase 2 in progress: P2-01 to P2-07 done; design spec approved for implementation (stack: Python 3.13 / uv / stdlib sqlite3 / pytest + ruff / GitHub Actions; `/harness` activation, phase state machine, task classification and local SQLite state with per-task workspaces, read-only repository analyzer, adaptive interview engine with unresolved-decision checklist, text architecture design with delegation marking and coverage check, Git-versioned decision registry with automatic recording, supersede history and read-only cross-project suggestions, specification manager with immutable approved versions tied to a commit, test planner with justified mocks and an approved plan embedded in the spec, final design review gate that blocks entering approval while anything is open, CLI approval gate whose approval record is the only handoff authorization, resume from committed checkpoints with a resumable-task list, a recorded demo, and a dogfood run that produced the approved [Phase 2 spec](docs/specs/t-a3752edf/v1.md); see the [dogfood log](docs/dogfood/phase-2-spec-run.md)). Next: P2-08 Phase 2 acceptance.

## Demo

[`docs/demo.md`](docs/demo.md) is a recorded transcript of one task going through the whole Phase 1 flow, from `harness start` to `harness approve`, mapped to the V1 acceptance criteria. Reproduce it with:

```bash
uv run python scripts/demo.py
```

The script runs in a temporary directory with its own throwaway `HARNESS_HOME` and a small temporary git repository, and deletes both afterwards, so it never touches real harness state or this repository's files. `tests/test_demo.py` keeps the transcript in `docs/demo.md` in sync with what the script prints.

## Dogfooding

Phase 1 was accepted by using the harness on its own repository to produce the specification for Phase 2:

- The interview answers were quoted only from `docs/SPEC.md` or `tasks/phase-2-visual-design-studio.md`, with file and line, or were the developer's own words.
- The session was interrupted with Q19 asked and no answer recorded, then continued with `harness resume`, which showed the same open question.
- Every decision, design entry, test-plan item and approval was given by the developer.
- 96 quoted spans in the spec were checked against their cited source lines, with no mismatches.

Results: the approved [Phase 2 spec](docs/specs/t-a3752edf/v1.md), the full [dogfood log](docs/dogfood/phase-2-spec-run.md), and the decision records [DEC-0005 to DEC-0018](docs/decisions/README.md).
