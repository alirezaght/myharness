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

## Approve & Implement

`harness approve <id> [--specs PATH]` is the developer-facing "Approve & Implement" step (SPEC §3 Phase 7). It is allowed only in the `approval` phase and, in one transaction, seals a new `v<N>.md` exactly as `spec approve` does (same output, including the superseded line for the previous version) and records the approval. It also re-runs the final design review and writes nothing (no file, row, event or checkpoint) if anything blocks, the task is outside `approval`, or there is no commit at HEAD. Success also prints `approval #<n> recorded: spec v<N> <content hash> (implementation handoff authorized; AL launch is Phase 3)`. The harness does not launch AL yet: that is Phase 3.

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

Phase 1 done (P1-01 to P1-13); design spec approved for implementation (stack: Python 3.13 / uv / stdlib sqlite3 / pytest + ruff / GitHub Actions; `/harness` activation, phase state machine, task classification and local SQLite state with per-task workspaces, read-only repository analyzer, adaptive interview engine with unresolved-decision checklist, text architecture design with delegation marking and coverage check, Git-versioned decision registry with automatic recording, supersede history and read-only cross-project suggestions, specification manager with immutable approved versions tied to a commit, test planner with justified mocks and an approved plan embedded in the spec, final design review gate that blocks entering approval while anything is open, CLI approval gate whose approval record is the only handoff authorization, resume from committed checkpoints with a resumable-task list, a recorded demo, and a dogfood run that produced the approved [Phase 2 spec](docs/specs/t-a3752edf/v1.md); see the [dogfood log](docs/dogfood/phase-2-spec-run.md)). Next: Phase 2 (Visual Design Studio).

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
