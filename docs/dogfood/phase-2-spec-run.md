# Dogfood run: the Phase 2 specification

This is the transcript of P1-13's dogfood run: the harness, run on its own repository, produced the specification for Phase 2 (Visual Design Studio). It is also Phase 1's acceptance check: a real task taken from start to an approved spec, with the interview interrupted about halfway and resumed.

- **Task:** `t-a3752edf`, request "Produce the specification for Phase 2 — Visual Design Studio", complexity `complex` (chosen by the developer).
- **Date:** 2026-10-17 · **Harness revision:** `7cb03f7` (P1-12).
- **Result:** [`docs/specs/t-a3752edf/v1.md`](../specs/t-a3752edf/v1.md), approved and current; decisions [DEC-0005](../decisions/DEC-0005-phase-2-scope-boundary.md) to [DEC-0018](../decisions/DEC-0018-phase-2-verification-approach.md).
- **Answer sourcing rule (developer's choice):** the AL supervisor answered interview questions only by quoting `docs/SPEC.md` or `tasks/phase-2-visual-design-studio.md`, with file and line. Anything those files did not settle went to the developer and was recorded in the developer's words. Every decision, design entry, test-plan item and approval was given by the developer; each verdict is quoted below in the step it applies to.
- **Paths:** the absolute repository path is shown as `<repo>` and the home directory as `~`. Everything else is verbatim harness output.
- **Local state:** the task's SQLite database lives under `~/.local/share/harness/` and is private to the developer (SPEC §8.10, AC 27); it is not committed.
- **Why the spec header says "14 uncommitted paths":** the spec was sealed at `7cb03f7` while the 14 new decision records (DEC-0005 to DEC-0018) were still uncommitted; they are committed together with the spec.

## Transcript

### Start, investigation and interview batch 1 questions

```text
$ harness start "Produce the specification for Phase 2 — Visual Design Studio"
task t-a3752edf created · phase investigation · complexity standard (proposed)
override: harness classify t-a3752edf <trivial|simple|standard|complex>

$ harness investigate t-a3752edf
investigation of <repo> (revision 7cb03f761b47c2bed42fff2c256367ce1e6ccbcc)
request: Produce the specification for Phase 2 — Visual Design Studio
approved decisions (4):
  DEC-0001 v1 — Runtime and language
  DEC-0002 v1 — Packaging and entry point
  DEC-0003 v1 — SQLite access
  DEC-0004 v1 — Testing, lint and CI
verified facts (76):
  [repository] 480 files enumerated via git ls-files — git ls-files
  [repository] revision 7cb03f761b47c2bed42fff2c256367ce1e6ccbcc — git rev-parse HEAD
  [repository] branch main — git rev-parse --abbrev-ref HEAD
  [repository] working tree clean — git status --porcelain
  [decisions] DEC-0001 v1 approved — Runtime and language (scope: Whole harness; all phases) — docs/decisions/DEC-0001-runtime-and-language.md
  [decisions] DEC-0002 v1 approved — Packaging and entry point (scope: Harness distribution and activation (SPEC §2, AC 1)) — docs/decisions/DEC-0002-packaging-and-entry-point.md
  [decisions] DEC-0003 v1 approved — SQLite access (scope: Local runtime state (SPEC §7)) — docs/decisions/DEC-0003-sqlite-access.md
  [decisions] DEC-0004 v1 approved — Testing, lint and CI (scope: Repository quality gates) — docs/decisions/DEC-0004-testing-lint-ci.md
  [repository] 362 python files — file extensions
  [repository] 8 sql files — file extensions
  [dependencies] dependency manifest `pyproject.toml` — pyproject.toml
  [dependencies] runtime dependencies: (none) — pyproject.toml [project].dependencies
  [dependencies] dependency-groups `dev`: pytest>=8, ruff>=0.9 — pyproject.toml [dependency-groups]
  [constraints] requires-python >=3.13 — pyproject.toml [project]
  [interfaces] console script `harness` → harness.cli:main — pyproject.toml [project.scripts]
  [conventions] ruff line-length 100 — pyproject.toml [tool.ruff]
  [conventions] ruff lint rules: E, F, I, UP, B — pyproject.toml [tool.ruff]
  [tests] pytest testpaths: tests — pyproject.toml [tool.pytest.ini_options]
  [components] `harness/` is a Python package (62 source files) — harness/__init__.py
  [files] `README.md` matches 6 request keywords (produce, specification, phase, visual, design, studio) — keyword match: produce, specification, phase, visual, design, studio
  [files] `docs/ROADMAP.md` matches 6 request keywords (produce, specification, phase, visual, design, studio) — keyword match: produce, specification, phase, visual, design, studio
  [files] `al/claude/skills/al/SKILL.md` matches 5 request keywords (produce, specification, phase, visual, design) — keyword match: produce, specification, phase, visual, design
  [files] `al/codex/skills/al/SKILL.md` matches 5 request keywords (produce, specification, phase, visual, design) — keyword match: produce, specification, phase, visual, design
  [files] `docs/SPEC.md` matches 5 request keywords (produce, specification, phase, visual, design) — keyword match: produce, specification, phase, visual, design
  [files] `tasks/phase-2-visual-design-studio.md` matches 4 request keywords (phase, visual, design, studio) — keyword match: phase, visual, design, studio (path: phase, visual, design, studio)
  [files] `tasks/phase-1-core-harness.md` matches 4 request keywords (produce, specification, phase, design) — keyword match: produce, specification, phase, design (path: phase)
  [files] `al/claude/agents/al-architect.md` matches 4 request keywords (produce, specification, phase, design) — keyword match: produce, specification, phase, design
  [files] `al/claude/skills/al/reference/parallelism.md` matches 4 request keywords (produce, specification, phase, design) — keyword match: produce, specification, phase, design
  [files] `al/claude/skills/al/reference/routing.md` matches 4 request keywords (produce, specification, phase, design) — keyword match: produce, specification, phase, design
  [files] `al/codex/agents/al-architect.toml` matches 4 request keywords (produce, specification, phase, design) — keyword match: produce, specification, phase, design
  [files] `al/codex/skills/al/reference/parallelism.md` matches 4 request keywords (produce, specification, phase, design) — keyword match: produce, specification, phase, design
  [files] `al/codex/skills/al/reference/routing.md` matches 4 request keywords (produce, specification, phase, design) — keyword match: produce, specification, phase, design
  [files] `al/claude/agents/al-designer.md` matches 3 request keywords (produce, specification, design) — keyword match: produce, specification, design (path: design)
  [files] `al/codex/agents/al-designer.toml` matches 3 request keywords (produce, specification, design) — keyword match: produce, specification, design (path: design)
  [files] `.claude/commands/harness.md` matches 3 request keywords (specification, phase, design) — keyword match: specification, phase, design
  [files] `al/claude/agents/al-architect-fallback.md` matches 3 request keywords (specification, phase, design) — keyword match: specification, phase, design
  [files] `al/claude/agents/al-builder.md` matches 3 request keywords (specification, phase, design) — keyword match: specification, phase, design
  [files] `al/claude/agents/al-engineer.md` matches 3 request keywords (specification, phase, design) — keyword match: specification, phase, design
  [files] `al/claude/skills/al/reference/handoffs.md` matches 3 request keywords (specification, phase, design) — keyword match: specification, phase, design
  [interfaces] `al/claude/skills/al/scripts/al_affected_services.py` looks like an interface — name: service
  [interfaces] `al/claude/skills/al/scripts/al_schema.py` looks like an interface — name: schema
  [interfaces] `al/codex/skills/al/scripts/al_affected_services.py` looks like an interface — name: service
  [interfaces] `al/codex/skills/al/scripts/al_schema.py` looks like an interface — name: schema
  [interfaces] `harness/cli.py` looks like an interface — name: cli
  [interfaces] `harness/cli_approve.py` looks like an interface — name: cli
  [interfaces] `harness/cli_commands.py` looks like an interface — name: cli
  [interfaces] `harness/cli_decision.py` looks like an interface — name: cli
  [interfaces] `harness/cli_design.py` looks like an interface — name: cli
  [interfaces] `harness/cli_interview.py` looks like an interface — name: cli
  [interfaces] `harness/cli_resume.py` looks like an interface — name: cli
  [interfaces] `harness/cli_review.py` looks like an interface — name: cli
  [interfaces] `harness/cli_spec.py` looks like an interface — name: cli
  [interfaces] `harness/cli_testplan.py` looks like an interface — name: cli
  [interfaces] `harness/controller.py` looks like an interface — name: controller
  [schemas] schema file `harness/migrations/0001_tasks.sql` — harness/migrations/0001_tasks.sql
  [schemas] schema file `harness/migrations/0002_workspace.sql` — harness/migrations/0002_workspace.sql
  [schemas] schema file `harness/migrations/0003_interview.sql` — harness/migrations/0003_interview.sql
  [schemas] schema file `harness/migrations/0004_design.sql` — harness/migrations/0004_design.sql
  [schemas] schema file `harness/migrations/0005_decision_changes.sql` — harness/migrations/0005_decision_changes.sql
  [schemas] schema file `harness/migrations/0006_spec_versions.sql` — harness/migrations/0006_spec_versions.sql
  [schemas] schema file `harness/migrations/0007_test_plan.sql` — harness/migrations/0007_test_plan.sql
  [schemas] schema file `harness/migrations/0008_approvals.sql` — harness/migrations/0008_approvals.sql
  [schemas] migrations directory `harness/migrations/` (9 files) — harness/migrations
  [config] config file `al/codex/agents/al-architect-fallback.toml` — al/codex/agents/al-architect-fallback.toml
  [config] config file `al/codex/agents/al-architect.toml` — al/codex/agents/al-architect.toml
  [config] config file `al/codex/agents/al-builder.toml` — al/codex/agents/al-builder.toml
  [config] config file `al/codex/agents/al-designer-fallback.toml` — al/codex/agents/al-designer-fallback.toml
  [config] config file `al/codex/agents/al-designer.toml` — al/codex/agents/al-designer.toml
  [config] config file `al/codex/agents/al-engineer.toml` — al/codex/agents/al-engineer.toml
  [config] config file `al/codex/agents/al-scout.toml` — al/codex/agents/al-scout.toml
  [tests] 179 test files — test directory and file naming
  [tests] test location `al/claude/skills/al/tests/` (77 files) — al/claude/skills/al/tests
  [tests] test location `al/codex/skills/al/tests/` (73 files) — al/codex/skills/al/tests
  [tests] test location `tests/` (29 files) — tests
  [constraints] CI workflow `.github/workflows/ci.yml` — .github/workflows/ci.yml
  [conventions] Python 3.13 pinned — .python-version
assumptions (4):
  primary language is python — basis: majority of source files
  `al/` is a component (129 source files) — basis: directory layout
  the change most likely concerns `al/` — basis: highest request-keyword density
  new tests belong under `al/claude/skills/al/tests/` — basis: where most existing tests live
unresolved questions (0):
  (none)

$ harness classify t-a3752edf complex
task t-a3752edf · phase investigation · complexity complex (developer)

$ harness phase t-a3752edf next
task t-a3752edf · phase interview · complexity complex (developer)

$ harness interview next t-a3752edf --group
Q1 [business_requirements] What business problem does this solve, and for whom?
Q2 [business_requirements] (decision) What is explicitly out of scope for this change?
  recommendation needed: harness interview propose t-a3752edf 2 --title ... --recommendation ... --rationale ...
```

### user verdict batch 1 (2026-10-17): "q5 no limit, q7 explain more . the rest are fine"

```text
$ harness interview answer t-a3752edf 1 ""a local web UI where the developer reviews the spec and diagrams, comments on components or connections, gets redesigns, and approves with one button." (tasks/phase-2-visual-design-studio.md Goal, line 3); "Its primary purpose is to eliminate ambiguity before implementation begins." (docs/SPEC.md §1, line 19)"
Q1 answered

$ harness interview next t-a3752edf --group
Q2 [business_requirements] (decision) What is explicitly out of scope for this change?
  recommendation needed: harness interview propose t-a3752edf 2 --title ... --recommendation ... --rationale ...

$ harness interview propose t-a3752edf 2 --title "Phase 2 scope boundary" --recommendation "Phase 2 covers only its listed spec refs (SPEC §3 steps 4 and 7, §8.1, §8.6, §8.7, §8.9, §13 Diagram Service and Web Dashboard). Out of scope: execution monitoring (agent monitoring, progress tracking, orchestration view, live activity feed, SPEC §8.2-8.5), GitLab and delivery monitoring (§8.8), any AL launch (Phase 3), and direct diagram editing." --rationale ""**Spec refs:** §3 (steps 4, 7), §8.1, §8.6, §8.7, §8.9, §13 (Diagram Service, Web Dashboard)" (tasks/phase-2-visual-design-studio.md, line 4); "Direct diagram editing is not required for V1." (docs/SPEC.md §8.6, line 605)" --impact "Keeps Phase 2 a visual review/approval studio; monitoring and delivery views wait for Phases 3-4."
P1 pending for Q2

$ harness interview decide t-a3752edf 1 accept --response "the rest are fine (user, batch 1)"
P1 accepted
recorded DEC-0005 v1 -> <repo>/docs/decisions/DEC-0005-phase-2-scope-boundary.md

$ harness interview next t-a3752edf --group
Q3 [functional_behavior] What exactly should happen on the success path?
Q4 [functional_behavior] (decision) How should existing behaviour change, if at all?
  recommendation needed: harness interview propose t-a3752edf 4 --title ... --recommendation ... --rationale ...

$ harness interview answer t-a3752edf 3 ""Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48); "`/harness` opens the dashboard for the active task." (tasks/phase-2-visual-design-studio.md P2-01, line 14)"
Q3 answered

$ harness interview propose t-a3752edf 4 --title "Phase 1 CLI flow stays unchanged alongside the dashboard" --recommendation "The Phase 1 CLI flow stays unchanged; the dashboard works on the same SQLite state and Markdown specs, and its approval writes the same immutable snapshot as the CLI gate." --rationale ""No regressions in the Phase 1 CLI flow" (tasks/phase-2-visual-design-studio.md P2-08, line 49); "Reads and writes the same SQLite state and Markdown specs as the CLI" (line 11); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (line 45)" --impact "CLI and dashboard are two front ends over one state; no CLI behaviour or output changes."
P2 pending for Q4

$ harness interview decide t-a3752edf 2 accept --response "the rest are fine (user, batch 1)"
P2 accepted
recorded DEC-0006 v1 -> <repo>/docs/decisions/DEC-0006-phase-1-cli-flow-stays-unchanged-alongsi.md

$ harness interview next t-a3752edf --group
Q5 [inputs_outputs] Which inputs does it take, with which formats and limits?
Q6 [inputs_outputs] (decision) Which outputs or side effects does it produce?
  recommendation needed: harness interview propose t-a3752edf 6 --title ... --recommendation ... --rationale ...

$ harness interview answer t-a3752edf 5 ""Select a component or connection and add a comment (clarification / correction / redesign)" (tasks/phase-2-visual-design-studio.md P2-05, line 33). Limits (developer): "q5 no limit""
Q5 answered

$ harness interview propose t-a3752edf 6 --title "Dashboard outputs and side effects" --recommendation "The dashboard writes only: comments in the task's SQLite state tied to the element id and diagram version, versioned diagrams, and the approval snapshot. Nothing else is written." --rationale ""Comments persist in task state and are tied to the element id and the diagram version" (tasks/phase-2-visual-design-studio.md P2-05, line 34); "Version history for diagrams" (line 28); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (line 45)" --impact "Bounds the dashboard write surface to task state, diagram versions and the existing approval snapshot."
P3 pending for Q6

$ harness interview decide t-a3752edf 3 accept --response "the rest are fine (user, batch 1)"
P3 accepted
recorded DEC-0007 v1 -> <repo>/docs/decisions/DEC-0007-dashboard-outputs-and-side-effects.md

$ harness interview next t-a3752edf --group
Q7 [edge_cases] Which boundary or empty/oversized inputs must be handled?
Q8 [edge_cases] (decision) How should concurrent or conflicting operations behave?
  recommendation needed: harness interview propose t-a3752edf 8 --title ... --recommendation ... --rationale ...

$ harness interview propose t-a3752edf 8 --title "Concurrent CLI and dashboard operations" --recommendation "CLI and dashboard may act on the same task. Every write is one SQLite transaction (DEC-0003). The dashboard re-reads after each write and marks outdated data; a comment or approval that refers to a diagram version that is no longer current is refused with a reload hint." --rationale ""The dashboard must clearly distinguish current information from stale or unavailable status data." (docs/SPEC.md §8.11, line 661). The rest is the harness recommendation; the sources do not settle it." --impact "No lost or misattributed comments when CLI and dashboard are used together."
P4 pending for Q8

$ harness interview decide t-a3752edf 4 accept --response "the rest are fine (user, batch 1)"
P4 accepted
recorded DEC-0008 v1 -> <repo>/docs/decisions/DEC-0008-concurrent-cli-and-dashboard-operations.md

$ harness interview next t-a3752edf --group
Q7 [edge_cases] Which boundary or empty/oversized inputs must be handled?

$ harness interview propose t-a3752edf 9 --title "Diagram Service and Web Dashboard responsibilities" --recommendation "The Diagram Service owns diagram generation and revision; the Web Dashboard owns review, comments and approvals. Neither may bypass the approval gate, and neither edits diagrams directly." --rationale ""Generates and revises architecture diagrams." (docs/SPEC.md §13 Diagram Service, line 829); "Provides architecture review, monitoring, and approvals." (docs/SPEC.md §13 Web Dashboard, line 849); "- No bypass of approval gates through automatic execution modes" (docs/SPEC.md §11, line 751); "Direct diagram editing is not required for V1." (docs/SPEC.md §8.6, line 605)" --impact "Clear ownership; approvals stay behind the same gate as the CLI."
P5 pending for Q9

$ harness interview decide t-a3752edf 5 accept --response "the rest are fine (user, batch 1)"
P5 accepted
recorded DEC-0009 v1 -> <repo>/docs/decisions/DEC-0009-diagram-service-and-web-dashboard-respon.md

$ harness interview answer t-a3752edf 10 ""Lightweight local server plus frontend, launched from `/harness` (opens in the browser)" (tasks/phase-2-visual-design-studio.md P2-01, line 10); "Generates and revises architecture diagrams." (docs/SPEC.md §13 Diagram Service, line 829); "Provides architecture review, monitoring, and approvals." (docs/SPEC.md §13 Web Dashboard, line 849)"
Q10 answered
```

### user verdict batch 2 (2026-10-17): "free port. the suggestions are good"

```text
$ harness interview answer t-a3752edf 7 "Developer-approved boundary cases ("the suggestions are good"): (a) no design entries yet: show "no diagrams yet" instead of an empty or broken view; (b) large designs (100+ components or connections): diagrams still render and stay selectable, no size cap; (c) an element removed by a redesign: its comment stays visible, tied to its old diagram version, and can still be resolved or re-opened; (d) no active or unknown task: show the task list or "no tasks" instead of an error; (e) spec not drafted yet: the spec view shows "not drafted yet" and the current phase."
Q7 answered

$ harness interview propose t-a3752edf 11 --title "Dashboard component communication" --recommendation "The browser talks to a local server over HTTP on localhost; the server calls the harness engines and SQLite in-process; live updates are pushed by the server. The exact framework stays open (SPEC §15)." --rationale ""The dashboard receives live status updates without requiring manual page refreshes." (docs/SPEC.md §8.11, line 657); "Reads and writes the same SQLite state and Markdown specs as the CLI" (tasks/phase-2-visual-design-studio.md P2-01, line 11)" --impact "One local process owns state access; the browser never touches SQLite directly."
P6 pending for Q11

$ harness interview decide t-a3752edf 6 accept --response "the suggestions are good (user, batch 2)"
P6 accepted
recorded DEC-0010 v1 -> <repo>/docs/decisions/DEC-0010-dashboard-component-communication.md

$ harness interview answer t-a3752edf 12 ""The button stays disabled while comments are open, blocking questions remain or the final review fails" (tasks/phase-2-visual-design-studio.md P2-07, line 44); "approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48)"
Q12 answered

$ harness interview propose t-a3752edf 13 --title "Diagram and comment storage" --recommendation "A new migration adds diagram versions (built from a structured model with stable ids for components and connections) and comments tied to the element id and diagram version, with states open, addressed, resolved. No existing table changes." --rationale ""Diagrams come from a structured model (stable ids for components and connections) so comments can be anchored to them" (tasks/phase-2-visual-design-studio.md P2-04, line 27); "Comments persist in task state and are tied to the element id and the diagram version" (line 34); "Comment states: open → addressed → resolved" (line 35)" --impact "Additive schema change only; Phase 1 tables untouched."
P7 pending for Q13

$ harness interview decide t-a3752edf 7 accept --response "the suggestions are good (user, batch 2)"
P7 accepted
recorded DEC-0011 v1 -> <repo>/docs/decisions/DEC-0011-diagram-and-comment-storage.md

$ harness interview skip t-a3752edf 14 --source codebase --ref "schema changes ship as a new numbered migration (harness/migrations/0001-0008) applied on connect, upgrading existing state DBs and keeping their rows; existing tables stay as they are (developer: the suggestions are good)"
Q14 skipped (codebase schema changes ship as a new numbered migration (harness/migrations/0001-0008) applied on connect, upgrading existing state DBs and keeping their rows; existing tables stay as they are (developer: the suggestions are good))

$ harness interview propose t-a3752edf 15 --title "Local dashboard HTTP API" --recommendation "A new local HTTP API used only by the dashboard frontend: read task, spec, decisions and diagrams; add a comment; resolve or re-open a comment; approve. No CLI command changes." --rationale ""Select a component or connection and add a comment (clarification / correction / redesign)" (tasks/phase-2-visual-design-studio.md P2-05, line 33); "The developer resolves or re-opens each comment (AC 8)" (line 40); "No regressions in the Phase 1 CLI flow" (line 49)" --impact "Internal localhost API, no external consumers; CLI contract unchanged."
P8 pending for Q15

$ harness interview decide t-a3752edf 8 accept --response "the suggestions are good (user, batch 2)"
P8 accepted
recorded DEC-0012 v1 -> <repo>/docs/decisions/DEC-0012-local-dashboard-http-api.md

$ harness interview answer t-a3752edf 16 ""No regressions in the Phase 1 CLI flow" (tasks/phase-2-visual-design-studio.md P2-08, line 49). Developer-approved reading: every harness command, /harness (.claude/commands/harness.md) and require_approval keep working unchanged."
Q16 answered

$ harness interview answer t-a3752edf 17 ""Binds to localhost only" (tasks/phase-2-visual-design-studio.md P2-01, line 12). Port (developer): "free port" (a free port is chosen automatically each run)."
Q17 answered

$ harness interview propose t-a3752edf 18 --title "No feature flag for the dashboard" --recommendation "No feature flag: the dashboard only runs when launched from /harness, and the CLI is unaffected." --rationale ""The Harness activates only when explicitly invoked by the developer." (docs/SPEC.md §2, line 46); "Lightweight local server plus frontend, launched from `/harness` (opens in the browser)" (tasks/phase-2-visual-design-studio.md P2-01, line 10)" --impact "Explicit invocation is the switch; no extra configuration."
P9 pending for Q18

$ harness interview decide t-a3752edf 9 accept --response "the suggestions are good (user, batch 2)"
P9 accepted
recorded DEC-0013 v1 -> <repo>/docs/decisions/DEC-0013-no-feature-flag-for-the-dashboard.md

$ harness interview next t-a3752edf
Q19 [error_handling] (decision) How should failures be handled (retry, fail fast, degrade)?
  recommendation needed: harness interview propose t-a3752edf 19 --title ... --recommendation ... --rationale ...
```

### Session interrupted here: Q19 asked, no answer recorded (interruption requested from the developer)

### After the interruption: the developer's next message arrived in a new turn; continuing only from harness output

```text
$ harness resume
t-a3752edf · phase interview · complexity complex · updated 2026-10-17T11:17:22+00:00
  Produce the specification for Phase 2 — Visual Design Studio
  next: harness interview next t-a3752edf

$ harness resume t-a3752edf
task t-a3752edf · phase interview · complexity complex (developer)
request: Produce the specification for Phase 2 — Visual Design Studio
checkpoint: interview 2026-10-17T11:17:22+00:00 (#35)
Q19 [error_handling] (decision) How should failures be handled (retry, fail fast, degrade)?
  recommendation needed: harness interview propose t-a3752edf 19 --title ... --recommendation ... --rationale ...
next: harness interview next t-a3752edf

$ harness interview next t-a3752edf
Q19 [error_handling] (decision) How should failures be handled (retry, fail fast, degrade)?
  recommendation needed: harness interview propose t-a3752edf 19 --title ... --recommendation ... --rationale ...
```

### user answer Q19 (2026-10-17): "failure should be hadled by retry"

```text
$ harness interview propose t-a3752edf 19 --title "Failures are handled by retry" --recommendation "Failures are handled by retry." --rationale "Developer statement: "failure should be hadled by retry". The sources do not settle it; docs/SPEC.md §12 lists failure handling per area but not for the dashboard." --impact "Dashboard operations retry on failure instead of failing fast."
P10 pending for Q19

$ harness interview decide t-a3752edf 10 accept --response "failure should be hadled by retry"
P10 accepted
recorded DEC-0014 v1 -> <repo>/docs/decisions/DEC-0014-failures-are-handled-by-retry.md
```

### user verdict batch 3 (2026-10-17): "all good"

```text
$ harness interview answer t-a3752edf 20 ""Report the limitation clearly." (docs/SPEC.md §12, line 793); "Do not fabricate successful execution or validation." (docs/SPEC.md §12, line 795); "The dashboard must clearly distinguish current information from stale or unavailable status data." (docs/SPEC.md §8.11, line 661)"
Q20 answered

$ harness interview answer t-a3752edf 21 "Developer-approved ("all good"): no explicit latency, throughput or size targets for V1 (local single-user tool); large designs must still render and stay selectable (see Q7 b)."
Q21 answered

$ harness interview propose t-a3752edf 22 --title "No extra limits or caching for the dashboard" --recommendation "No limits, caching or batching beyond generating only the relevant diagrams; comments stay unlimited (Q5)." --rationale ""Only relevant diagrams should be generated." (docs/SPEC.md §3 Phase 4, line 157); developer on comment limits: "q5 no limit"" --impact "Simple implementation; no cache invalidation concerns."
P11 pending for Q22

$ harness interview decide t-a3752edf 11 accept --response "all good (user, batch 3)"
P11 accepted
recorded DEC-0015 v1 -> <repo>/docs/decisions/DEC-0015-no-extra-limits-or-caching-for-the-dashb.md

$ harness interview propose t-a3752edf 23 --title "Only the local developer may act in the dashboard" --recommendation "Only the local developer may act: enforced by binding to localhost only and by approvals going through the same gate as the CLI. No login." --rationale ""Binds to localhost only" (tasks/phase-2-visual-design-studio.md P2-01, line 12); "Each developer sees their own local Harness execution state." (docs/SPEC.md §8.10, line 649); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (tasks/phase-2-visual-design-studio.md P2-07, line 45)" --impact "No authentication layer; exposure limited to the local machine."
P12 pending for Q23

$ harness interview decide t-a3752edf 12 accept --response "all good (user, batch 3)"
P12 accepted
recorded DEC-0016 v1 -> <repo>/docs/decisions/DEC-0016-only-the-local-developer-may-act-in-the.md

$ harness interview answer t-a3752edf 24 ""- No secrets in Git-versioned specifications" (docs/SPEC.md §11, line 746); "Local SQLite runtime data is not shared automatically." (docs/SPEC.md §8.10, line 651)"
Q24 answered

$ harness interview answer t-a3752edf 25 "Developer-approved ("all good"): comments, redesigns and approvals are recorded as events in the task's existing event log, covering "- Audit records for significant operations" (docs/SPEC.md §11, line 750); no separate metrics or traces."
Q25 answered

$ harness interview propose t-a3752edf 26 --title "No alerting for the local dashboard" --recommendation "No alerting: this is a local single-user tool; failures are shown in the dashboard as in Q20." --rationale ""Report the limitation clearly." (docs/SPEC.md §12, line 793)" --impact "No alert channels to configure."
P13 pending for Q26

$ harness interview decide t-a3752edf 13 accept --response "all good (user, batch 3)"
P13 accepted
recorded DEC-0017 v1 -> <repo>/docs/decisions/DEC-0017-no-alerting-for-the-local-dashboard.md

$ harness interview answer t-a3752edf 27 ""Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48); "No regressions in the Phase 1 CLI flow" (line 49); "Docs and demo" (line 50); "`/harness` opens the dashboard for the active task." (P2-01 Done when, line 14); "a sample design renders all relevant diagram types with addressable elements." (P2-04 Done when, line 30)"
Q27 answered

$ harness interview propose t-a3752edf 28 --title "Phase 2 verification approach" --recommendation "Automated tests for each Phase 2 task, an end-to-end test of the wrong-connection scenario, the Phase 1 test suite as the regression check, and a recorded demo." --rationale ""Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48); "No regressions in the Phase 1 CLI flow" (line 49); "Docs and demo" (line 50)" --impact "Each acceptance criterion has an automated check plus a demo."
P14 pending for Q28

$ harness interview decide t-a3752edf 14 accept --response "all good (user, batch 3)"
P14 accepted
recorded DEC-0018 v1 -> <repo>/docs/decisions/DEC-0018-phase-2-verification-approach.md

$ harness interview checklist t-a3752edf
unresolved decisions (0):
  (none)
blocking: no

$ harness interview next t-a3752edf
no open questions

$ harness phase t-a3752edf next
task t-a3752edf · phase design · complexity complex (developer)

$ harness design coverage t-a3752edf
uncovered/unresolved design items (8):
  section:components [components] uncovered
  section:responsibilities [responsibilities] uncovered
  section:interactions [interactions] uncovered
  section:data_flow [data_flow] uncovered
  section:interfaces [interfaces] uncovered
  section:configuration [configuration] uncovered
  section:failure_handling [failure_handling] uncovered
  section:required_behaviour [required_behaviour] uncovered
blocking: yes
```

### user verdict design (2026-10-17): "all accept"

```text
$ harness design propose t-a3752edf components "A Web Dashboard (local server plus browser frontend, launched from /harness), a Diagram Service, and new storage for diagram versions and comments; the existing harness engines (spec, decisions, review, approval) are reused." --rationale "DEC-0009, DEC-0010, DEC-0011; "Lightweight local server plus frontend, launched from `/harness` (opens in the browser)" (tasks/phase-2-visual-design-studio.md P2-01, line 10)"
E1 pending [components]
  decide: harness design decide t-a3752edf 1 accept|reject|modify

$ harness design decide t-a3752edf 1 accept --response "all accept (user, design)"
E1 accepted

$ harness design propose t-a3752edf responsibilities "The Diagram Service generates and revises diagrams; the Dashboard handles review, comments, approvals, the task overview and task switching; neither bypasses the approval gate or edits diagrams directly." --rationale "DEC-0009; "Task name, description, current phase, status, elapsed time, pending approvals, latest activity (§8.1)" (tasks/phase-2-visual-design-studio.md P2-02, line 17)"
E2 pending [responsibilities]
  decide: harness design decide t-a3752edf 2 accept|reject|modify

$ harness design decide t-a3752edf 2 accept --response "all accept (user, design)"
E2 accepted

$ harness design propose t-a3752edf interactions "The browser talks to the local server over HTTP on localhost with server-pushed live updates; the server calls the engines and SQLite in-process; redesign loop: the harness takes open comments, updates the design and regenerates the diagrams." --rationale "DEC-0010; "The harness takes open comments, updates the design and regenerates the diagrams" (tasks/phase-2-visual-design-studio.md P2-06, line 38)"
E3 pending [interactions]
  decide: harness design decide t-a3752edf 3 accept|reject|modify

$ harness design decide t-a3752edf 3 accept --response "all accept (user, design)"
E3 accepted

$ harness design propose t-a3752edf data_flow "Design entries become a structured model with stable ids, which the Diagram Service turns into versioned diagrams shown in the Dashboard; comments (element id plus diagram version) are stored and a redesign produces a new version; approval writes the same snapshot as the CLI." --rationale "DEC-0007, DEC-0011; "Diagrams come from a structured model (stable ids for components and connections) so comments can be anchored to them" (tasks/phase-2-visual-design-studio.md P2-04, line 27)"
E4 pending [data_flow]
  decide: harness design decide t-a3752edf 4 accept|reject|modify

$ harness design decide t-a3752edf 4 accept --response "all accept (user, design)"
E4 accepted

$ harness design propose t-a3752edf interfaces "A local HTTP API used only by the frontend (read task, spec, decisions and diagrams; comment; resolve or re-open; approve); /harness opens the dashboard in the browser; all CLI commands unchanged." --rationale "DEC-0012; "`/harness` opens the dashboard for the active task." (tasks/phase-2-visual-design-studio.md P2-01, line 14)"
E5 pending [interfaces]
  decide: harness design decide t-a3752edf 5 accept|reject|modify

$ harness design decide t-a3752edf 5 accept --response "all accept (user, design)"
E5 accepted

$ harness design propose t-a3752edf configuration "Localhost only, a free port chosen each run, no feature flag, no other settings." --rationale "DEC-0013; "Binds to localhost only" (tasks/phase-2-visual-design-studio.md P2-01, line 12); developer: "free port""
E6 pending [configuration]
  decide: harness design decide t-a3752edf 6 accept|reject|modify

$ harness design decide t-a3752edf 6 accept --response "all accept (user, design)"
E6 accepted

$ harness design propose t-a3752edf failure_handling "Retry on failure; report limitations clearly and never fabricate success; mark stale data; refuse a comment or approval against an outdated diagram version with a reload hint." --rationale "DEC-0014, DEC-0008; "Report the limitation clearly." (docs/SPEC.md §12, line 793)"
E7 pending [failure_handling]
  decide: harness design decide t-a3752edf 7 accept|reject|modify

$ harness design decide t-a3752edf 7 accept --response "all accept (user, design)"
E7 accepted

$ harness design propose t-a3752edf required_behaviour "Approve & Implement stays disabled while comments are open, blocking questions remain or the final review fails; revised diagrams appear next to the previous version with the change highlighted; comments move open, addressed, resolved and can be re-opened; only relevant diagram types are generated; approved and open decisions are shown separately with a spec version diff; the five Q7 boundary cases are handled." --rationale ""The button stays disabled while comments are open, blocking questions remain or the final review fails" (tasks/phase-2-visual-design-studio.md P2-07, line 44); "Show revised diagrams next to the previous version, with the change highlighted" (line 39); Q7 boundary cases"
E8 pending [required_behaviour]
  decide: harness design decide t-a3752edf 8 accept|reject|modify

$ harness design decide t-a3752edf 8 accept --response "all accept (user, design)"
E8 accepted

$ harness design propose t-a3752edf components --delegated "The exact backend framework, frontend framework and diagram renderer are chosen by AL during implementation." --rationale "docs/SPEC.md §15 leaves "- A specific backend framework" (line 916) and "- A specific frontend framework" (line 917) open"
E9 pending [components] (delegated)
  decide: harness design decide t-a3752edf 9 accept|reject|modify

$ harness design decide t-a3752edf 9 accept --response "all accept (user, design)"
E9 accepted

$ harness design coverage t-a3752edf
uncovered/unresolved design items (0):
  (none)
blocking: no

$ harness phase t-a3752edf next
task t-a3752edf · phase test_plan · complexity complex (developer)

$ harness testplan review t-a3752edf
test plan review (0 effective scenario(s)):
  plan not_approved
categories without scenarios: unit, integration, e2e, regression, edge_case, failure (not required)
approval: none
blocking: yes
```

### user verdict test scenarios (2026-10-17): "accept"

```text
$ harness testplan propose t-a3752edf unit "The Diagram Service builds the structured model with stable ids and generates only the relevant diagram types, with addressable elements." --rationale "DEC-0018; "a sample design renders all relevant diagram types with addressable elements." (tasks/phase-2-visual-design-studio.md P2-04, line 30)" --covers E4
S1 pending [unit]
  decide: harness testplan decide t-a3752edf 1 accept|reject|modify

$ harness testplan decide t-a3752edf 1 accept --response "accept (user, test scenarios)"
S1 accepted

$ harness testplan propose t-a3752edf unit "Comment states move open, addressed, resolved, and a resolved comment can be re-opened." --rationale "DEC-0011; "Comment states: open → addressed → resolved" (tasks/phase-2-visual-design-studio.md P2-05, line 35)" --covers Q13
S2 pending [unit]
  decide: harness testplan decide t-a3752edf 2 accept|reject|modify

$ harness testplan decide t-a3752edf 2 accept --response "accept (user, test scenarios)"
S2 accepted

$ harness testplan propose t-a3752edf integration "The dashboard server reads and writes the same SQLite state and specs as the CLI; a comment added through the API is stored with its element id and diagram version." --rationale "DEC-0006; "Reads and writes the same SQLite state and Markdown specs as the CLI" (tasks/phase-2-visual-design-studio.md P2-01, line 11)" --covers Q4
S3 pending [integration]
  decide: harness testplan decide t-a3752edf 3 accept|reject|modify

$ harness testplan decide t-a3752edf 3 accept --response "accept (user, test scenarios)"
S3 accepted

$ harness testplan propose t-a3752edf integration "The server binds to localhost only on a free port, and /harness opens the dashboard for the active task." --rationale "DEC-0013; "Binds to localhost only" (tasks/phase-2-visual-design-studio.md P2-01, line 12)" --covers Q17
S4 pending [integration]
  decide: harness testplan decide t-a3752edf 4 accept|reject|modify

$ harness testplan decide t-a3752edf 4 accept --response "accept (user, test scenarios)"
S4 accepted

$ harness testplan propose t-a3752edf e2e "Wrong-connection scenario: comment, harness fixes it, Approve stays disabled until every comment is resolved, approval writes the same snapshot as the CLI." --rationale "DEC-0018; "Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48)" --covers Q27
S5 pending [e2e]
  decide: harness testplan decide t-a3752edf 5 accept|reject|modify

$ harness testplan decide t-a3752edf 5 accept --response "accept (user, test scenarios)"
S5 accepted

$ harness testplan propose t-a3752edf regression "The full Phase 1 test suite passes unchanged." --rationale "DEC-0018; "No regressions in the Phase 1 CLI flow" (tasks/phase-2-visual-design-studio.md P2-08, line 49)" --covers Q16
S6 pending [regression]
  decide: harness testplan decide t-a3752edf 6 accept|reject|modify

$ harness testplan decide t-a3752edf 6 accept --response "accept (user, test scenarios)"
S6 accepted

$ harness testplan propose t-a3752edf edge_case "The five Q7 boundary cases: no design yet, a large design, an element removed in a redesign, no task, spec not drafted." --rationale "Q7 boundary cases (developer: "the suggestions are good")" --covers Q7
S7 pending [edge_case]
  decide: harness testplan decide t-a3752edf 7 accept|reject|modify

$ harness testplan decide t-a3752edf 7 accept --response "accept (user, test scenarios)"
S7 accepted

$ harness testplan propose t-a3752edf edge_case "A comment or approval against an outdated diagram version is refused with a reload hint." --rationale DEC-0008 --covers Q8
S8 pending [edge_case]
  decide: harness testplan decide t-a3752edf 8 accept|reject|modify

$ harness testplan decide t-a3752edf 8 accept --response "accept (user, test scenarios)"
S8 accepted

$ harness testplan propose t-a3752edf failure "A failing operation is retried; if it still fails the dashboard reports it clearly, never claims success, and marks stale data." --rationale "DEC-0014; "Do not fabricate successful execution or validation." (docs/SPEC.md §12, line 795)" --covers Q19
S9 pending [failure]
  decide: harness testplan decide t-a3752edf 9 accept|reject|modify

$ harness testplan decide t-a3752edf 9 accept --response "accept (user, test scenarios)"
S9 accepted

$ harness testplan review t-a3752edf
test plan review (9 effective scenario(s)):
  plan not_approved
(every category has a scenario)
approval: none
blocking: yes
```

### user approval test plan (2026-10-17): "approve"

```text
$ harness testplan approve t-a3752edf
approved test plan (9 scenario(s), sha256:4b790720c9ddd1ae1359b487ac7e0b12c9e4595571867de7b88ced0420b43461)

$ harness testplan review t-a3752edf
test plan review (9 effective scenario(s)):
  (none)
(every category has a scenario)
approval: 2026-10-17T11:22:42+00:00 sha256:4b790720c9ddd1ae1359b487ac7e0b12c9e4595571867de7b88ced0420b43461
blocking: no

$ harness phase t-a3752edf next
task t-a3752edf · phase final_review · complexity complex (developer)

$ harness review t-a3752edf
final design review (SPEC §3 step 6):
  ok      Requirements are clear
  ok      Significant decisions are approved
  ok      Architecture is consistent
  ok      Component responsibilities are defined
  ok      Component interactions are understood
  ok      Acceptance criteria are testable
  ok      The test plan is sufficient
  ok      No blocking questions remain
  n/a     No unresolved diagram comments remain — diagrams and comments are Phase 2
  ok      No known conflicts are ignored
blocking: no

$ harness phase t-a3752edf next
task t-a3752edf · phase approval · complexity complex (developer)
final design review passed (checkpoint recorded)

$ harness spec show t-a3752edf
# Specification — Produce the specification for Phase 2 — Visual Design Studio

- **Task:** t-a3752edf
- **Status:** draft
- **Version:** draft
- **Timestamp:** 2026-10-17T11:22:47+00:00
- **Complexity:** complex (developer)
- **Revision:** 7cb03f761b47c2bed42fff2c256367ce1e6ccbcc
- **Working tree:** 14 uncommitted paths
- **Supersedes:** none

## Requirements

- Q1 [business_requirements] "a local web UI where the developer reviews the spec and diagrams, comments on components or connections, gets redesigns, and approves with one button." (tasks/phase-2-visual-design-studio.md Goal, line 3); "Its primary purpose is to eliminate ambiguity before implementation begins." (docs/SPEC.md §1, line 19) (developer)
- Q2 [business_requirements] Phase 2 covers only its listed spec refs (SPEC §3 steps 4 and 7, §8.1, §8.6, §8.7, §8.9, §13 Diagram Service and Web Dashboard). Out of scope: execution monitoring (agent monitoring, progress tracking, orchestration view, live activity feed, SPEC §8.2-8.5), GitLab and delivery monitoring (§8.8), any AL launch (Phase 3), and direct diagram editing. (accepted_recommendation)
- Q3 [functional_behavior] "Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48); "`/harness` opens the dashboard for the active task." (tasks/phase-2-visual-design-studio.md P2-01, line 14) (developer)
- Q4 [functional_behavior] The Phase 1 CLI flow stays unchanged; the dashboard works on the same SQLite state and Markdown specs, and its approval writes the same immutable snapshot as the CLI gate. (accepted_recommendation)
- Q5 [inputs_outputs] "Select a component or connection and add a comment (clarification / correction / redesign)" (tasks/phase-2-visual-design-studio.md P2-05, line 33). Limits (developer): "q5 no limit" (developer)
- Q6 [inputs_outputs] The dashboard writes only: comments in the task's SQLite state tied to the element id and diagram version, versioned diagrams, and the approval snapshot. Nothing else is written. (accepted_recommendation)
- Q7 [edge_cases] Developer-approved boundary cases ("the suggestions are good"): (a) no design entries yet: show "no diagrams yet" instead of an empty or broken view; (b) large designs (100+ components or connections): diagrams still render and stay selectable, no size cap; (c) an element removed by a redesign: its comment stays visible, tied to its old diagram version, and can still be resolved or re-opened; (d) no active or unknown task: show the task list or "no tasks" instead of an error; (e) spec not drafted yet: the spec view shows "not drafted yet" and the current phase. (developer)
- Q8 [edge_cases] CLI and dashboard may act on the same task. Every write is one SQLite transaction (DEC-0003). The dashboard re-reads after each write and marks outdated data; a comment or approval that refers to a diagram version that is no longer current is refused with a reload hint. (accepted_recommendation)
- Q9 [component_responsibilities] The Diagram Service owns diagram generation and revision; the Web Dashboard owns review, comments and approvals. Neither may bypass the approval gate, and neither edits diagrams directly. (accepted_recommendation)
- Q10 [component_responsibilities] "Lightweight local server plus frontend, launched from `/harness` (opens in the browser)" (tasks/phase-2-visual-design-studio.md P2-01, line 10); "Generates and revises architecture diagrams." (docs/SPEC.md §13 Diagram Service, line 829); "Provides architecture review, monitoring, and approvals." (docs/SPEC.md §13 Web Dashboard, line 849) (developer)
- Q11 [component_communication] The browser talks to a local server over HTTP on localhost; the server calls the harness engines and SQLite in-process; live updates are pushed by the server. The exact framework stays open (SPEC §15). (accepted_recommendation)
- Q12 [component_communication] "The button stays disabled while comments are open, blocking questions remain or the final review fails" (tasks/phase-2-visual-design-studio.md P2-07, line 44); "approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48) (developer)
- Q13 [data_schemas] A new migration adds diagram versions (built from a structured model with stable ids for components and connections) and comments tied to the element id and diagram version, with states open, addressed, resolved. No existing table changes. (accepted_recommendation)
- Q15 [api_contracts] A new local HTTP API used only by the dashboard frontend: read task, spec, decisions and diagrams; add a comment; resolve or re-open a comment; approve. No CLI command changes. (accepted_recommendation)
- Q16 [api_contracts] "No regressions in the Phase 1 CLI flow" (tasks/phase-2-visual-design-studio.md P2-08, line 49). Developer-approved reading: every harness command, /harness (.claude/commands/harness.md) and require_approval keep working unchanged. (developer)
- Q17 [configuration] "Binds to localhost only" (tasks/phase-2-visual-design-studio.md P2-01, line 12). Port (developer): "free port" (a free port is chosen automatically each run). (developer)
- Q18 [configuration] No feature flag: the dashboard only runs when launched from /harness, and the CLI is unaffected. (accepted_recommendation)
- Q19 [error_handling] Failures are handled by retry. (accepted_recommendation)
- Q20 [error_handling] "Report the limitation clearly." (docs/SPEC.md §12, line 793); "Do not fabricate successful execution or validation." (docs/SPEC.md §12, line 795); "The dashboard must clearly distinguish current information from stale or unavailable status data." (docs/SPEC.md §8.11, line 661) (developer)
- Q21 [performance] Developer-approved ("all good"): no explicit latency, throughput or size targets for V1 (local single-user tool); large designs must still render and stay selectable (see Q7 b). (developer)
- Q22 [performance] No limits, caching or batching beyond generating only the relevant diagrams; comments stay unlimited (Q5). (accepted_recommendation)
- Q23 [security] Only the local developer may act: enforced by binding to localhost only and by approvals going through the same gate as the CLI. No login. (accepted_recommendation)
- Q24 [security] "- No secrets in Git-versioned specifications" (docs/SPEC.md §11, line 746); "Local SQLite runtime data is not shared automatically." (docs/SPEC.md §8.10, line 651) (developer)
- Q25 [observability] Developer-approved ("all good"): comments, redesigns and approvals are recorded as events in the task's existing event log, covering "- Audit records for significant operations" (docs/SPEC.md §11, line 750); no separate metrics or traces. (developer)
- Q26 [observability] No alerting: this is a local single-user tool; failures are shown in the dashboard as in Q20. (accepted_recommendation)

## Architecture

### Responsibilities
- The Diagram Service generates and revises diagrams; the Dashboard handles review, comments, approvals, the task overview and task switching; neither bypasses the approval gate or edits diagrams directly. (fixed; accepted_recommendation)
### Interactions
- The browser talks to the local server over HTTP on localhost with server-pushed live updates; the server calls the engines and SQLite in-process; redesign loop: the harness takes open comments, updates the design and regenerates the diagrams. (fixed; accepted_recommendation)
### Data flow
- Design entries become a structured model with stable ids, which the Diagram Service turns into versioned diagrams shown in the Dashboard; comments (element id plus diagram version) are stored and a redesign produces a new version; approval writes the same snapshot as the CLI. (fixed; accepted_recommendation)
### Failure handling
- Retry on failure; report limitations clearly and never fabricate success; mark stale data; refuse a comment or approval against an outdated diagram version with a reload hint. (fixed; accepted_recommendation)
### Required behaviour
- Approve & Implement stays disabled while comments are open, blocking questions remain or the final review fails; revised diagrams appear next to the previous version with the change highlighted; comments move open, addressed, resolved and can be re-opened; only relevant diagram types are generated; approved and open decisions are shown separately with a spec version diff; the five Q7 boundary cases are handled. (fixed; accepted_recommendation)
### Delegated to AL
- [Components] The exact backend framework, frontend framework and diagram renderer are chosen by AL during implementation. (accepted_recommendation)

## Components and interfaces

### Components
- A Web Dashboard (local server plus browser frontend, launched from /harness), a Diagram Service, and new storage for diagram versions and comments; the existing harness engines (spec, decisions, review, approval) are reused. (fixed; accepted_recommendation)
- The exact backend framework, frontend framework and diagram renderer are chosen by AL during implementation. (delegated to AL; accepted_recommendation)
### Interfaces and contracts
- A local HTTP API used only by the frontend (read task, spec, decisions and diagrams; comment; resolve or re-open; approve); /harness opens the dashboard in the browser; all CLI commands unchanged. (fixed; accepted_recommendation)

## Configuration

- Localhost only, a free port chosen each run, no feature flag, no other settings. (fixed; accepted_recommendation)

## Acceptance criteria

- Q27 [acceptance_criteria] "Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48); "No regressions in the Phase 1 CLI flow" (line 49); "Docs and demo" (line 50); "`/harness` opens the dashboard for the active task." (P2-01 Done when, line 14); "a sample design renders all relevant diagram types with addressable elements." (P2-04 Done when, line 30) (developer)
- Q28 [acceptance_criteria] Automated tests for each Phase 2 task, an end-to-end test of the wrong-connection scenario, the Phase 1 test suite as the regression check, and a recorded demo. (accepted_recommendation)

## Test plan

- approved 2026-10-17T11:22:42+00:00 (9 scenario(s); sha256:4b790720c9ddd1ae1359b487ac7e0b12c9e4595571867de7b88ced0420b43461)
### Unit tests
- S1 The Diagram Service builds the structured model with stable ids and generates only the relevant diagram types, with addressable elements. — covers E4 — mock: none (accepted_recommendation)
- S2 Comment states move open, addressed, resolved, and a resolved comment can be re-opened. — covers Q13 — mock: none (accepted_recommendation)
### Integration tests
- S3 The dashboard server reads and writes the same SQLite state and specs as the CLI; a comment added through the API is stored with its element id and diagram version. — covers Q4 — mock: none (accepted_recommendation)
- S4 The server binds to localhost only on a free port, and /harness opens the dashboard for the active task. — covers Q17 — mock: none (accepted_recommendation)
### End-to-end tests
- S5 Wrong-connection scenario: comment, harness fixes it, Approve stays disabled until every comment is resolved, approval writes the same snapshot as the CLI. — covers Q27 — mock: none (accepted_recommendation)
### Regression tests
- S6 The full Phase 1 test suite passes unchanged. — covers Q16 — mock: none (accepted_recommendation)
### Edge cases
- S7 The five Q7 boundary cases: no design yet, a large design, an element removed in a redesign, no task, spec not drafted. — covers Q7 — mock: none (accepted_recommendation)
- S8 A comment or approval against an outdated diagram version is refused with a reload hint. — covers Q8 — mock: none (accepted_recommendation)
### Failure scenarios
- S9 A failing operation is retried; if it still fails the dashboard reports it clearly, never claims success, and marks stale data. — covers Q19 — mock: none (accepted_recommendation)

## Rationale

- P1 Phase 2 scope boundary: "**Spec refs:** §3 (steps 4, 7), §8.1, §8.6, §8.7, §8.9, §13 (Diagram Service, Web Dashboard)" (tasks/phase-2-visual-design-studio.md, line 4); "Direct diagram editing is not required for V1." (docs/SPEC.md §8.6, line 605) (accepted)
- P2 Phase 1 CLI flow stays unchanged alongside the dashboard: "No regressions in the Phase 1 CLI flow" (tasks/phase-2-visual-design-studio.md P2-08, line 49); "Reads and writes the same SQLite state and Markdown specs as the CLI" (line 11); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (line 45) (accepted)
- P3 Dashboard outputs and side effects: "Comments persist in task state and are tied to the element id and the diagram version" (tasks/phase-2-visual-design-studio.md P2-05, line 34); "Version history for diagrams" (line 28); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (line 45) (accepted)
- P4 Concurrent CLI and dashboard operations: "The dashboard must clearly distinguish current information from stale or unavailable status data." (docs/SPEC.md §8.11, line 661). The rest is the harness recommendation; the sources do not settle it. (accepted)
- P5 Diagram Service and Web Dashboard responsibilities: "Generates and revises architecture diagrams." (docs/SPEC.md §13 Diagram Service, line 829); "Provides architecture review, monitoring, and approvals." (docs/SPEC.md §13 Web Dashboard, line 849); "- No bypass of approval gates through automatic execution modes" (docs/SPEC.md §11, line 751); "Direct diagram editing is not required for V1." (docs/SPEC.md §8.6, line 605) (accepted)
- P6 Dashboard component communication: "The dashboard receives live status updates without requiring manual page refreshes." (docs/SPEC.md §8.11, line 657); "Reads and writes the same SQLite state and Markdown specs as the CLI" (tasks/phase-2-visual-design-studio.md P2-01, line 11) (accepted)
- P7 Diagram and comment storage: "Diagrams come from a structured model (stable ids for components and connections) so comments can be anchored to them" (tasks/phase-2-visual-design-studio.md P2-04, line 27); "Comments persist in task state and are tied to the element id and the diagram version" (line 34); "Comment states: open → addressed → resolved" (line 35) (accepted)
- P8 Local dashboard HTTP API: "Select a component or connection and add a comment (clarification / correction / redesign)" (tasks/phase-2-visual-design-studio.md P2-05, line 33); "The developer resolves or re-opens each comment (AC 8)" (line 40); "No regressions in the Phase 1 CLI flow" (line 49) (accepted)
- P9 No feature flag for the dashboard: "The Harness activates only when explicitly invoked by the developer." (docs/SPEC.md §2, line 46); "Lightweight local server plus frontend, launched from `/harness` (opens in the browser)" (tasks/phase-2-visual-design-studio.md P2-01, line 10) (accepted)
- P10 Failures are handled by retry: Developer statement: "failure should be hadled by retry". The sources do not settle it; docs/SPEC.md §12 lists failure handling per area but not for the dashboard. (accepted)
- P11 No extra limits or caching for the dashboard: "Only relevant diagrams should be generated." (docs/SPEC.md §3 Phase 4, line 157); developer on comment limits: "q5 no limit" (accepted)
- P12 Only the local developer may act in the dashboard: "Binds to localhost only" (tasks/phase-2-visual-design-studio.md P2-01, line 12); "Each developer sees their own local Harness execution state." (docs/SPEC.md §8.10, line 649); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (tasks/phase-2-visual-design-studio.md P2-07, line 45) (accepted)
- P13 No alerting for the local dashboard: "Report the limitation clearly." (docs/SPEC.md §12, line 793) (accepted)
- P14 Phase 2 verification approach: "Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48); "No regressions in the Phase 1 CLI flow" (line 49); "Docs and demo" (line 50) (accepted)
- E1 [Components] A Web Dashboard (local server plus browser frontend, launched from /harness), a Diagram Service, and new storage for diagram versions and comments; the existing harness engines (spec, decisions, review, approval) are reused.: DEC-0009, DEC-0010, DEC-0011; "Lightweight local server plus frontend, launched from `/harness` (opens in the browser)" (tasks/phase-2-visual-design-studio.md P2-01, line 10) (accepted_recommendation)
- E2 [Responsibilities] The Diagram Service generates and revises diagrams; the Dashboard handles review, comments, approvals, the task overview and task switching; neither bypasses the approval gate or edits diagrams directly.: DEC-0009; "Task name, description, current phase, status, elapsed time, pending approvals, latest activity (§8.1)" (tasks/phase-2-visual-design-studio.md P2-02, line 17) (accepted_recommendation)
- E3 [Interactions] The browser talks to the local server over HTTP on localhost with server-pushed live updates; the server calls the engines and SQLite in-process; redesign loop: the harness takes open comments, updates the design and regenerates the diagrams.: DEC-0010; "The harness takes open comments, updates the design and regenerates the diagrams" (tasks/phase-2-visual-design-studio.md P2-06, line 38) (accepted_recommendation)
- E4 [Data flow] Design entries become a structured model with stable ids, which the Diagram Service turns into versioned diagrams shown in the Dashboard; comments (element id plus diagram version) are stored and a redesign produces a new version; approval writes the same snapshot as the CLI.: DEC-0007, DEC-0011; "Diagrams come from a structured model (stable ids for components and connections) so comments can be anchored to them" (tasks/phase-2-visual-design-studio.md P2-04, line 27) (accepted_recommendation)
- E5 [Interfaces and contracts] A local HTTP API used only by the frontend (read task, spec, decisions and diagrams; comment; resolve or re-open; approve); /harness opens the dashboard in the browser; all CLI commands unchanged.: DEC-0012; "`/harness` opens the dashboard for the active task." (tasks/phase-2-visual-design-studio.md P2-01, line 14) (accepted_recommendation)
- E6 [Configuration] Localhost only, a free port chosen each run, no feature flag, no other settings.: DEC-0013; "Binds to localhost only" (tasks/phase-2-visual-design-studio.md P2-01, line 12); developer: "free port" (accepted_recommendation)
- E7 [Failure handling] Retry on failure; report limitations clearly and never fabricate success; mark stale data; refuse a comment or approval against an outdated diagram version with a reload hint.: DEC-0014, DEC-0008; "Report the limitation clearly." (docs/SPEC.md §12, line 793) (accepted_recommendation)
- E8 [Required behaviour] Approve & Implement stays disabled while comments are open, blocking questions remain or the final review fails; revised diagrams appear next to the previous version with the change highlighted; comments move open, addressed, resolved and can be re-opened; only relevant diagram types are generated; approved and open decisions are shown separately with a spec version diff; the five Q7 boundary cases are handled.: "The button stays disabled while comments are open, blocking questions remain or the final review fails" (tasks/phase-2-visual-design-studio.md P2-07, line 44); "Show revised diagrams next to the previous version, with the change highlighted" (line 39); Q7 boundary cases (accepted_recommendation)
- E9 [Components] The exact backend framework, frontend framework and diagram renderer are chosen by AL during implementation.: docs/SPEC.md §15 leaves "- A specific backend framework" (line 916) and "- A specific frontend framework" (line 917) open (accepted_recommendation)
- DEC-0005 v1 — Phase 2 scope boundary: "**Spec refs:** §3 (steps 4, 7), §8.1, §8.6, §8.7, §8.9, §13 (Diagram Service, Web Dashboard)" (tasks/phase-2-visual-design-studio.md, line 4); "Direct diagram editing is not required for V1." (docs/SPEC.md §8.6, line 605)
- DEC-0006 v1 — Phase 1 CLI flow stays unchanged alongside the dashboard: "No regressions in the Phase 1 CLI flow" (tasks/phase-2-visual-design-studio.md P2-08, line 49); "Reads and writes the same SQLite state and Markdown specs as the CLI" (line 11); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (line 45)
- DEC-0007 v1 — Dashboard outputs and side effects: "Comments persist in task state and are tied to the element id and the diagram version" (tasks/phase-2-visual-design-studio.md P2-05, line 34); "Version history for diagrams" (line 28); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (line 45)
- DEC-0008 v1 — Concurrent CLI and dashboard operations: "The dashboard must clearly distinguish current information from stale or unavailable status data." (docs/SPEC.md §8.11, line 661). The rest is the harness recommendation; the sources do not settle it.
- DEC-0009 v1 — Diagram Service and Web Dashboard responsibilities: "Generates and revises architecture diagrams." (docs/SPEC.md §13 Diagram Service, line 829); "Provides architecture review, monitoring, and approvals." (docs/SPEC.md §13 Web Dashboard, line 849); "- No bypass of approval gates through automatic execution modes" (docs/SPEC.md §11, line 751); "Direct diagram editing is not required for V1." (docs/SPEC.md §8.6, line 605)
- DEC-0010 v1 — Dashboard component communication: "The dashboard receives live status updates without requiring manual page refreshes." (docs/SPEC.md §8.11, line 657); "Reads and writes the same SQLite state and Markdown specs as the CLI" (tasks/phase-2-visual-design-studio.md P2-01, line 11)
- DEC-0011 v1 — Diagram and comment storage: "Diagrams come from a structured model (stable ids for components and connections) so comments can be anchored to them" (tasks/phase-2-visual-design-studio.md P2-04, line 27); "Comments persist in task state and are tied to the element id and the diagram version" (line 34); "Comment states: open → addressed → resolved" (line 35)
- DEC-0012 v1 — Local dashboard HTTP API: "Select a component or connection and add a comment (clarification / correction / redesign)" (tasks/phase-2-visual-design-studio.md P2-05, line 33); "The developer resolves or re-opens each comment (AC 8)" (line 40); "No regressions in the Phase 1 CLI flow" (line 49)
- DEC-0013 v1 — No feature flag for the dashboard: "The Harness activates only when explicitly invoked by the developer." (docs/SPEC.md §2, line 46); "Lightweight local server plus frontend, launched from `/harness` (opens in the browser)" (tasks/phase-2-visual-design-studio.md P2-01, line 10)
- DEC-0014 v1 — Failures are handled by retry: Developer statement: "failure should be hadled by retry". The sources do not settle it; docs/SPEC.md §12 lists failure handling per area but not for the dashboard.
- DEC-0015 v1 — No extra limits or caching for the dashboard: "Only relevant diagrams should be generated." (docs/SPEC.md §3 Phase 4, line 157); developer on comment limits: "q5 no limit"
- DEC-0016 v1 — Only the local developer may act in the dashboard: "Binds to localhost only" (tasks/phase-2-visual-design-studio.md P2-01, line 12); "Each developer sees their own local Harness execution state." (docs/SPEC.md §8.10, line 649); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (tasks/phase-2-visual-design-studio.md P2-07, line 45)
- DEC-0017 v1 — No alerting for the local dashboard: "Report the limitation clearly." (docs/SPEC.md §12, line 793)
- DEC-0018 v1 — Phase 2 verification approach: "Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48); "No regressions in the Phase 1 CLI flow" (line 49); "Docs and demo" (line 50)

## Constraints

- DEC-0001 v1 — Runtime and language (Whole harness; all phases)
- DEC-0002 v1 — Packaging and entry point (Harness distribution and activation (SPEC §2, AC 1))
- DEC-0003 v1 — SQLite access (Local runtime state (SPEC §7))
- DEC-0004 v1 — Testing, lint and CI (Repository quality gates)
- Q14 [data_schemas] How must existing data be migrated or stay compatible? — codebase: schema changes ship as a new numbered migration (harness/migrations/0001-0008) applied on connect, upgrading existing state DBs and keeping their rows; existing tables stay as they are (developer: the suggestions are good)

## Unresolved items

### Interview
- (none)
- blocking: no
### Design
- (none)
- blocking: no
### Test plan
- (none)
- blocking: no
```

### user "Approve & Implement" (2026-10-17): "yes"

```text
$ harness approve t-a3752edf
approved v1 -> <repo>/docs/specs/t-a3752edf/v1.md (revision 7cb03f761b47c2bed42fff2c256367ce1e6ccbcc, 14 uncommitted paths)
approval #1 recorded: spec v1 sha256:b151e5b9533e5eeb08f84a7d5b78fe7827eb5a61f0334fc00a7d0275cc12cfb5 (implementation handoff authorized; AL launch is Phase 3)
```

## Interruption and resume

After 18 of the 28 interview questions were settled, `harness interview next` asked Q19 and the session stopped there with no answer recorded. The developer's next message arrived in a new turn. Before recording it, the supervisor worked only from what the harness printed:

| Step | Output line |
|---|---|
| `harness interview next` before the interruption | `Q19 [error_handling] (decision) How should failures be handled (retry, fail fast, degrade)?` |
| `harness resume` (list) | `next: harness interview next t-a3752edf` |
| `harness resume t-a3752edf` | `Q19 [error_handling] (decision) How should failures be handled (retry, fail fast, degrade)?` and `next: harness interview next t-a3752edf` |
| `harness interview next` after the interruption | `Q19 [error_handling] (decision) How should failures be handled (retry, fail fast, degrade)?` |

The same question came back in all three, still open, so the interview continued from exactly where it stopped. Each harness command is a separate process; the state survived only through the committed SQLite rows.

## Final evidence

```text
$ harness review t-a3752edf
final design review (SPEC §3 step 6):
  ok      Requirements are clear
  ok      Significant decisions are approved
  ok      Architecture is consistent
  ok      Component responsibilities are defined
  ok      Component interactions are understood
  ok      Acceptance criteria are testable
  ok      The test plan is sufficient
  ok      No blocking questions remain
  n/a     No unresolved diagram comments remain — diagrams and comments are Phase 2
  ok      No known conflicts are ignored
blocking: no

$ harness show t-a3752edf
task t-a3752edf · phase approval · complexity complex (developer)
request: Produce the specification for Phase 2 — Visual Design Studio
created: 2026-10-17T10:54:22+00:00 · updated: 2026-10-17T11:24:10+00:00
approval: spec v1 sha256:b151e5b9533e5eeb08f84a7d5b78fe7827eb5a61f0334fc00a7d0275cc12cfb5 (current)
interview topics: business_requirements, functional_behavior, inputs_outputs, edge_cases, component_responsibilities, component_communication, data_schemas, api_contracts, configuration, error_handling, performance, security, observability, acceptance_criteria
history:
  2026-10-17T10:54:22+00:00 - -> investigation (task created)
  2026-10-17T10:54:40+00:00 investigation -> interview
  2026-10-17T11:20:32+00:00 interview -> design
  2026-10-17T11:21:29+00:00 design -> test_plan
  2026-10-17T11:22:43+00:00 test_plan -> final_review
  2026-10-17T11:22:47+00:00 final_review -> approval

$ harness spec verify t-a3752edf
v1 ok

$ harness resume t-a3752edf
task t-a3752edf · phase approval · complexity complex (developer)
request: Produce the specification for Phase 2 — Visual Design Studio
checkpoint: approval 2026-10-17T11:24:10+00:00 (#92)
approval: spec v1 sha256:b151e5b9533e5eeb08f84a7d5b78fe7827eb5a61f0334fc00a7d0275cc12cfb5 (current)
nothing to resume: approved for implementation (handoff is Phase 3)

$ harness interview checklist t-a3752edf
unresolved decisions (0):
  (none)
blocking: no
```

Quote check (every quoted span with a line citation in v1.md compared against that line of its source file):

```text
quote check: 96 quoted spans match their cited source line, 0 mismatches
```

`git diff --exit-code 7cb03f7 -- docs/decisions/DEC-0001-*.md … DEC-0004-*.md` was clean: the four Phase 1 decisions were not modified.

## Observations

Notes for later phases; none of them blocked the run or is fixed here.

- The investigation's keyword relevance pointed at `al/` (the vendored AL package) as the most likely component, because `al/` holds most files. For a spec-only request in this repository the relevant files were `docs/SPEC.md` and `tasks/phase-2-visual-design-studio.md`, which it also listed.
- The final review reports diagram comments as `n/a` ("diagrams and comments are Phase 2"); the produced spec is exactly what Phase 2 will build to change that.
- `interview next` re-asks the first unanswered question, so a question awaiting a longer developer explanation (Q7 here) holds the queue; later questions could still be answered or proposed directly by id.
- With complexity `complex` the interview had 28 catalogue questions, 14 of them decisions; answering in batches per topic group kept the developer's effort to a few replies.

## Defects

None. No harness behaviour contradicted SPEC, README, `.claude/commands/harness.md` or the tests, so no harness code changed in P1-13.
