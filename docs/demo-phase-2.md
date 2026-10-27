# Phase 2 demo

`scripts/demo_phase2.py` runs the Phase 2 acceptance scenario (a diagram with a wrong connection,
a comment, the harness fixes it, approval only once every comment is resolved) on throwaway
state: its own small sample git repository, installed Claude AL package/roles and `HARNESS_HOME`,
all inside a temporary directory that is deleted afterwards. It never touches this repository's
`docs/`, `tasks/` or your default harness state. The harness CLI, the redesign tool (`python -m
harness.redesign`), the detached execution worker and the dashboard (`python -m harness.dashboard`)
run as subprocesses; the dashboard is driven over HTTP on `127.0.0.1` exactly as the page drives
it. Only the external Claude executable/model boundary is simulated by a local fixture; it emits
native session records but performs no AL work, so the approval-bound run correctly remains
Interrupted and resumable. Every synthetic answer, design entry, reason, test scenario and comment
starts with `[demo]`.

The `/harness` agent is not simulated: step 4 runs the commands that
`.claude/commands/harness.md` tells it to run in the redesign loop, and the developer's verdicts
(route back to design, `accept`) are scripted.

## What it shows

Mapped to the P2-08 task, the [ROADMAP](ROADMAP.md) Phase 2 acceptance check, the approved Phase 2
specification ([docs/specs/t-a3752edf/v1.md](specs/t-a3752edf/v1.md)) and its decisions:

| Step in the transcript | Covers |
| --- | --- |
| 1. A trivial task goes through the unchanged Phase 1 CLI flow into the approval phase; its components entry draws `connect Item Store -> Exporter` (the wrong direction) | P2-08 "No regressions in the Phase 1 CLI flow"; DEC-0006 |
| 2. The dashboard opens on the same state DB; diagram version 1 shows the wrong connection and Approve & Implement is enabled | DEC-0006, DEC-0007 |
| 3. The developer comments on the wrong connection (`POST /api/comments`); the button becomes disabled and `POST /api/approve` is refused with 409 | ROADMAP acceptance check; Q12 |
| 4. The harness lists the comment, routes back to design, proposes the corrected connection, records the accepted verdict, withdraws the wrong entry, addresses the comment (diagram version 2) and re-advances to approval; the final design review passes again | P2-08 scenario "harness fixes it"; spec S5 |
| 5. The comparison of versions 1 and 2 marks the wrong connection removed and the corrected one added; an addressed comment still blocks the button | spec S5; Q12 |
| 6. The developer resolves the comment; the button is enabled, the shared approval gate seals the spec and the real detached worker reaches the simulated external executable, which supplies no AL work | P2-08 "approval only becomes possible once every comment is resolved"; DEC-0006; Phase 3 resume contract |
| 7. With the dashboard stopped, `harness show`, `harness spec verify` and `harness resume` see the approval and reconnect to the interrupted run | DEC-0006; DEC-0008 (one shared state); Phase 3 resume contract |

The approve requests carry the diagram version, the final-review state hash and the hash of the
specification the page showed; a page that is no longer current is refused with a reload hint
(spec S8, DEC-0008). The transcript does not show that refusal; `tests/test_phase2_acceptance.py`
does, and it also checks that the dashboard approval writes the same snapshot as `harness
approve`, that resolving one of two comments keeps the button disabled, and that a refused
approval writes nothing. `tests/test_phase2_acceptance_browser.py` runs the comment, review,
resolve and approve steps in a real headless Chrome.

**Limitation (A2, DEC-0006, Q16).** Only the dashboard enforces "every diagram comment is
resolved". The CLI `harness approve` does not check diagram comments (`harness review` keeps
reporting `No unresolved diagram comments remain` as `n/a`, as in step 1), so it can approve while
a comment is unresolved. Use the dashboard button when comments matter.

## Reproduce

```bash
uv sync
uv run python scripts/demo_phase2.py
```

`python scripts/demo_phase2.py` works too: the script finds the repository root from its own path
and runs the harness with the interpreter that runs it. It needs `git` and a free loopback port;
nothing is reached beyond `127.0.0.1`. It reuses the Phase 1 demo's environment (see
[demo.md](demo.md#reproduce)):

- `HARNESS_PYTHON=/path/to/python` runs the harness with another interpreter.
- `KEEP_DEMO=1` keeps the temporary directory and prints its path on stderr.

The dashboard subprocess is always stopped, also when a step fails.

## Normalisation

Each CLI command is printed as `$ harness <args>` (the redesign tool and the dashboard as
`$ python -m harness.<module> <args>`) followed by its merged stdout and stderr. Each HTTP request
is printed as `$ <METHOD> <path> [<JSON body>]` followed by the status and the JSON response with
sorted keys. Three responses are shown as a selection, not in full:

- `GET /api/dashboard`: the current diagram version, the connections (`id`, `label`), the comment
  counts and the Approve & Implement state (`phase`, `enabled`, `blockers`, `comments`); the rest
  of the read model (elapsed time, latest activity, …) changes from run to run.
- `GET /api/diagrams/compare`: the list of changes and the marks per side.
- the successful `POST /api/approve`: approval output plus the reserved execution and launch
  request; the external executable response is intentionally not presented as AL completion.

Run-specific values are replaced as in the [Phase 1 demo](demo.md#normalisation), plus the port:

| Value | Shown as |
| --- | --- |
| the temporary directory | `<demo>` |
| ISO timestamps | `<timestamp>` |
| dates (`YYYY-MM-DD`) | `<date>` |
| content hashes `sha256:<64 hex>` | `sha256:<hash>` |
| 40-hex Git revisions | `<rev>` |
| task ids `t-<8 hex>` | `t-xxxxxxxx` |
| the dashboard address `127.0.0.1:<port>` | `127.0.0.1:<port>` |

Comment, design-entry, scenario and approval numbers and counts are left as they are.

## Screenshots

Screenshots are not committed: they are not byte-for-byte reproducible and no test could pin them.
The headless-Chrome acceptance test saves them locally when asked:

```bash
HARNESS_DASHBOARD_SCREENSHOT_DIR=/tmp/harness-shots uv run pytest tests/test_phase2_acceptance_browser.py
```

It writes `acceptance-comment-1280w.png` (the comment is open, Approve & Implement disabled) and
`acceptance-resolve-1280w.png` (version 2 next to version 1, the comment addressed).

## Regenerate

Replace the block below with the script's output; `tests/test_demo_phase2.py` fails when they
differ:

```bash
uv run python scripts/demo_phase2.py
```

## Transcript

<!-- demo-transcript:begin -->
```text
# AL Engineering Harness, Phase 2 demo (throwaway state; external provider boundary simulated)

## 1. Build a task whose design has a wrong connection

$ harness start --complexity trivial '[demo] Add a CSV export of items in app/export.py'
task t-xxxxxxxx created · phase investigation · complexity trivial (developer)
$ harness investigate t-xxxxxxxx
investigation of <demo>/repo (revision <rev>)
request: [demo] Add a CSV export of items in app/export.py
approved decisions (1):
  DEC-0001 v1 — [demo] Storage engine
verified facts (12):
  [repository] 4 files enumerated via git ls-files — git ls-files
  [repository] revision <rev> — git rev-parse HEAD
  [repository] branch main — git rev-parse --abbrev-ref HEAD
  [repository] working tree clean — git status --porcelain
  [repository] 0 ahead and 0 behind refs/heads/main — local Git ref comparison; remote freshness unverified (no fetch)
  [decisions] DEC-0001 v1 approved — [demo] Storage engine (scope: Persistence) — docs/decisions/DEC-0001-storage-engine.md
  [repository] 2 python files — file extensions
  [components] `app/` is a Python package (2 source files) — app/__init__.py
  [files] `docs/decisions/DEC-0001-storage-engine.md` matches 3 request keywords (demo, export, app) — keyword match: demo, export, app
  [files] `app/export.py` matches 2 request keywords (export, app) — keyword match: export, app (path: export, app)
  [files] `app/__init__.py` matches 1 request keyword (app) — keyword match: app (path: app)
  [files] `README.md` matches 1 request keyword (demo) — keyword match: demo
assumptions (1):
  primary language is python — basis: majority of source files
unresolved questions (2):
  [dependencies] No dependency manifest found; how are dependencies managed?
  [tests] No tests found; which framework and layout should new tests use?
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase interview · complexity trivial (developer)
$ harness interview next t-xxxxxxxx
Q1 [dependencies] No dependency manifest found; how are dependencies managed?
$ harness interview answer t-xxxxxxxx 1 '[demo] Standard library only, no new packages'
Q1 answered
$ harness interview next t-xxxxxxxx
Q2 [tests] No tests found; which framework and layout should new tests use?
$ harness interview answer t-xxxxxxxx 2 '[demo] pytest with tests/ at the repository root'
Q2 answered
$ harness interview next t-xxxxxxxx
Q3 [functional_behavior] What exactly should happen on the success path?
$ harness interview answer t-xxxxxxxx 3 '[demo] The exporter writes items.csv from the item store'
Q3 answered
$ harness interview next t-xxxxxxxx
Q4 [functional_behavior] (decision) How should existing behaviour change, if at all?
  recommendation needed: harness interview propose t-xxxxxxxx 4 --title ... --recommendation ... --rationale ...
$ harness interview answer t-xxxxxxxx 4 '[demo] Existing exports stay unchanged'
Q4 answered
$ harness interview next t-xxxxxxxx
Q5 [acceptance_criteria] What are the acceptance criteria for calling this done?
$ harness interview answer t-xxxxxxxx 5 '[demo] items.csv has one row per item'
Q5 answered
$ harness interview next t-xxxxxxxx
Q6 [acceptance_criteria] (decision) How will each criterion be verified?
  recommendation needed: harness interview propose t-xxxxxxxx 6 --title ... --recommendation ... --rationale ...
$ harness interview answer t-xxxxxxxx 6 '[demo] A unit test reads items.csv back'
Q6 answered
$ harness interview checklist t-xxxxxxxx
unresolved decisions (0):
  (none)
blocking: no
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase design · complexity trivial (developer)
After clarification, reassess inspected implementation risk: harness classify t-xxxxxxxx --assessment FILE --repo REPOSITORY
$ harness design add t-xxxxxxxx components '[demo] The exporter writes the item rows
component Exporter
component Item Store
connect Item Store -> Exporter: writes rows'
E1 stated [components] (fixed)
$ harness design none t-xxxxxxxx responsibilities --reason '[demo] Not needed here'
E2 [responsibilities] not applicable
$ harness design none t-xxxxxxxx interfaces --reason '[demo] Not needed here'
E3 [interfaces] not applicable
$ harness design none t-xxxxxxxx interactions --reason '[demo] Not needed here'
E4 [interactions] not applicable
$ harness design none t-xxxxxxxx data_flow --reason '[demo] Not needed here'
E5 [data_flow] not applicable
$ harness design none t-xxxxxxxx configuration --reason '[demo] Not needed here'
E6 [configuration] not applicable
$ harness design none t-xxxxxxxx failure_handling --reason '[demo] Not needed here'
E7 [failure_handling] not applicable
$ harness design none t-xxxxxxxx required_behaviour --reason '[demo] Not needed here'
E8 [required_behaviour] not applicable
$ harness design coverage t-xxxxxxxx
uncovered/unresolved design items (0):
  (none)
blocking: no
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase test_plan · complexity trivial (developer)
$ harness testplan add t-xxxxxxxx unit '[demo] A unit test reads items.csv back'
S1 stated [unit]
$ harness testplan approve t-xxxxxxxx
approved test plan (1 scenario(s), sha256:<hash>)
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase final_review · complexity trivial (developer)
$ harness review t-xxxxxxxx
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
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase approval · complexity trivial (developer)
final design review passed (checkpoint recorded)

## 2. Open the dashboard

$ python -m harness.dashboard t-xxxxxxxx --no-browser &
dashboard: http://127.0.0.1:<port>/ · task t-xxxxxxxx · Ctrl-C stops it
$ GET /api/dashboard?task=t-xxxxxxxx
200 {"comments": {"addressed": 0, "open": 0, "resolved": 0}, "connections": [{"id": "connection:item-store->exporter", "label": "Item Store -> Exporter"}], "diagram_version": 1, "final": {"blockers": [], "comments": {"addressed": 0, "open": 0}, "enabled": true, "phase": "approval"}}

## 3. Comment on the wrong connection

$ POST /api/comments {"element": "connection:item-store->exporter", "kind": "correction", "request_id": "demo-comment-0001", "task": "t-xxxxxxxx", "text": "[demo] The arrow points the wrong way: the exporter writes the rows", "version": 1}
201 {"comment": {"created_at": "<timestamp>", "element": "connection:item-store->exporter", "id": 1, "kind": "correction", "status": "open", "text": "[demo] The arrow points the wrong way: the exporter writes the rows", "updated_at": "<timestamp>", "version": 1}}
$ GET /api/dashboard?task=t-xxxxxxxx
200 {"comments": {"addressed": 0, "open": 1, "resolved": 0}, "connections": [{"id": "connection:item-store->exporter", "label": "Item Store -> Exporter"}], "diagram_version": 1, "final": {"blockers": [{"code": "comments", "text": "1 diagram comment(s) not resolved (open 1 · addressed 0)"}], "comments": {"addressed": 0, "open": 1}, "enabled": false, "phase": "approval"}}
$ POST /api/approve {"review_hash": "sha256:<hash>", "spec_hash": "sha256:<hash>", "task": "t-xxxxxxxx", "version": 1}
409 {"error": "1 diagram comment(s) are not resolved (open 1 · addressed 0); resolve every comment before approving"}

## 4. The harness fixes it (the developer's verdicts are scripted)

$ python -m harness.redesign t-xxxxxxxx
task t-xxxxxxxx · diagram version 1 (current) · comments: open 1 · addressed 0 · resolved 0
#1 · correction · connection Item Store -> Exporter (connection:item-store->exporter) · version 1
    [demo] The arrow points the wrong way: the exporter writes the rows
address: uv run python -m harness.redesign address t-xxxxxxxx <comment id>...
$ harness phase t-xxxxxxxx back design --reason '[demo] comment #1'
task t-xxxxxxxx · phase design · complexity trivial (developer)
$ harness design propose t-xxxxxxxx components '[demo] The exporter writes the item rows
component Exporter
component Item Store
connect Exporter -> Item Store: writes rows' --rationale '[demo] comment #1'
E9 pending [components]
  decide: harness design decide t-xxxxxxxx 9 accept|reject|modify
$ harness design decide t-xxxxxxxx 9 accept
E9 accepted
$ harness design withdraw t-xxxxxxxx 1
E1 withdrawn
$ python -m harness.redesign address t-xxxxxxxx 1
recorded diagram version 2
#1 addressed in diagram version 2
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase test_plan · complexity trivial (developer)
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase final_review · complexity trivial (developer)
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase approval · complexity trivial (developer)
final design review passed (checkpoint recorded)

## 5. Review the revision

$ GET /api/diagrams/compare?from=1&to=2&task=t-xxxxxxxx
200
  removed: connection Item Store -> Exporter (connection:item-store->exporter)
  added: connection Exporter -> Item Store (connection:exporter->item-store)
  marks from: {"connection:item-store->exporter": "removed"}
  marks to: {"connection:exporter->item-store": "added"}
$ GET /api/dashboard?task=t-xxxxxxxx
200 {"comments": {"addressed": 1, "open": 0, "resolved": 0}, "connections": [{"id": "connection:exporter->item-store", "label": "Exporter -> Item Store"}], "diagram_version": 2, "final": {"blockers": [{"code": "comments", "text": "1 diagram comment(s) not resolved (open 0 · addressed 1)"}], "comments": {"addressed": 1, "open": 0}, "enabled": false, "phase": "approval"}}

## 6. Resolve, then Approve & Implement

$ POST /api/comments/resolve {"comment": 1, "task": "t-xxxxxxxx"}
200 {"changed": true, "comment": {"created_at": "<timestamp>", "element": "connection:item-store->exporter", "id": 1, "kind": "correction", "status": "resolved", "text": "[demo] The arrow points the wrong way: the exporter writes the rows", "updated_at": "<timestamp>", "version": 1}}
$ GET /api/dashboard?task=t-xxxxxxxx
200 {"comments": {"addressed": 0, "open": 0, "resolved": 1}, "connections": [{"id": "connection:exporter->item-store", "label": "Exporter -> Item Store"}], "diagram_version": 2, "final": {"blockers": [], "comments": {"addressed": 0, "open": 0}, "enabled": true, "phase": "approval"}}
$ POST /api/approve {"review_hash": "sha256:<hash>", "spec_hash": "sha256:<hash>", "task": "t-xxxxxxxx", "version": 2}
201
  created: true
  approved v1 -> <demo>/repo/docs/specs/t-xxxxxxxx/v1.md (revision <rev>, clean)
  approval #1 recorded: spec v1 sha256:<hash> (implementation handoff authorized; AL launch is Phase 3)
  execution: exec-xxxxxxxxxxxxxxxxxxxxxxxx (WorkerOwned)
  launch: WorkerOwned (external provider boundary simulated)

## 7. Stop the dashboard; the CLI sees the approval

$ kill -TERM <dashboard>
dashboard stopped
$ harness show t-xxxxxxxx
task t-xxxxxxxx · phase approval · complexity trivial (developer)
request: [demo] Add a CSV export of items in app/export.py
created: <timestamp> · updated: <timestamp>
approval: spec v1 sha256:<hash> (current)
interview topics: functional_behavior, acceptance_criteria
history:
  <timestamp> - -> investigation (task created)
  <timestamp> investigation -> interview
  <timestamp> interview -> design
  <timestamp> design -> test_plan
  <timestamp> test_plan -> final_review
  <timestamp> final_review -> approval
  <timestamp> approval -> design ([demo] comment #1)
  <timestamp> design -> test_plan
  <timestamp> test_plan -> final_review
  <timestamp> final_review -> approval
$ harness spec verify t-xxxxxxxx
v1 ok
$ harness resume t-xxxxxxxx
task t-xxxxxxxx · phase approval · complexity trivial (developer)
request: [demo] Add a CSV export of items in app/export.py
checkpoint: approval <timestamp> (#30)
approval: spec v1 sha256:<hash> (current)
next: harness execution reconnect t-xxxxxxxx exec-xxxxxxxxxxxxxxxxxxxxxxxx
```
<!-- demo-transcript:end -->
