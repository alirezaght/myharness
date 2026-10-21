# Phase 1 demo

`scripts/demo.py` runs one task through the whole Phase 1 flow, from `harness start` to
`harness approve`, on throwaway state: its own small sample git repository and its own
`HARNESS_HOME`, both inside a temporary directory that is deleted afterwards. It never touches this
repository's `docs/`, `tasks/` or your default harness state. Every synthetic answer,
recommendation, design entry and test scenario starts with `[demo]`.

## What it shows

Mapped to the V1 acceptance criteria in [SPEC §14](SPEC.md#14-v1-acceptance-criteria):

| Step in the transcript | Acceptance criterion |
| --- | --- |
| `harness start` runs only on an explicit command; the complexity proposal is overridden with `classify` | 1, 3 |
| `harness investigate` reads the sample repository and its approved DEC-0001 before any question | 2 |
| The interview asks the questions the investigation could not settle; one answer is `unknown` and stays open | 3, 4 |
| A recommendation is proposed and only the developer's `accept` records it as DEC-0002 | 5, 13 |
| The session is interrupted with Q5 asked; `harness resume` lists the task, `harness resume <id>` shows the same Q5 and `interview next` asks it again | 12, 14 |
| The design has an accepted recommendation, a stated entry, a `--delegated` entry and explicit "not applicable" sections | 5, 6 |
| `harness approve` is refused (exit 1) before the approval phase | 11 |
| A test scenario is proposed, accepted, reviewed and the test plan approved | 9 |
| `harness review` runs the mandatory completeness review: `blocking: no` | 10 |
| After the decision is committed, `harness approve` seals `v1.md` tied to the revision and records approval #1; `show`, `spec verify` and `resume` confirm it | 6, 11, 30 |

The task state lives in a SQLite database under the temporary `HARNESS_HOME` (criterion 14); the
decision records and the sealed specification are Markdown files in the sample repository
(criterion 13).

## Reproduce

```bash
uv sync
uv run python scripts/demo.py
```

`python scripts/demo.py` works too: the script finds the repository root from its own path and runs
the harness with the interpreter that runs it (`<python> -m harness.cli`). It needs `git`.

- `HARNESS_PYTHON=/path/to/python` runs the harness with another interpreter.
- `KEEP_DEMO=1` keeps the temporary directory and prints its path on stderr.

Git runs with `GIT_CONFIG_GLOBAL` set to the null device, `GIT_CONFIG_NOSYSTEM=1` and a fixed
author, committer (`demo@example.invalid`) and date, so your own Git configuration does not leak in.
`HARNESS_DB` is ignored.

## Normalisation

Each command is printed as `$ harness <args>` followed by its merged stdout and stderr; a refused
command adds `(exit N)`. Run-specific values are replaced so the transcript is the same on every
run:

| Value | Shown as |
| --- | --- |
| the temporary directory | `<demo>` |
| ISO timestamps | `<timestamp>` |
| dates (`YYYY-MM-DD`) | `<date>` |
| content hashes `sha256:<64 hex>` | `sha256:<hash>` |
| 40-hex Git revisions | `<rev>` |
| task ids `t-<8 hex>` | `t-xxxxxxxx` |

Decision ids (DEC-000N), versions (vN), question, proposal, design-entry and scenario ids
(Q/P/E/S<n>), checkpoint and approval numbers (`#<n>`) and counts are left as they are.

## Regenerate

Replace the block below with the script's output; `tests/test_demo.py` fails when they differ:

```bash
uv run python scripts/demo.py
```

## Transcript

<!-- demo-transcript:begin -->
```text
# AL Engineering Harness, Phase 1 demo (throwaway state under <demo>)

## 1. Start a task and override its complexity

$ harness start '[demo] Add a CSV export of items'
task t-xxxxxxxx created · phase investigation · complexity standard (proposed)
override: harness classify t-xxxxxxxx <trivial|simple|standard|complex>
$ harness classify t-xxxxxxxx trivial
task t-xxxxxxxx · phase investigation · complexity trivial (developer)

## 2. Read-only investigation of the sample repository

$ harness investigate t-xxxxxxxx
investigation of <demo>/repo (revision <rev>)
request: [demo] Add a CSV export of items
approved decisions (1):
  DEC-0001 v1 — [demo] Storage engine
verified facts (10):
  [repository] 4 files enumerated via git ls-files — git ls-files
  [repository] revision <rev> — git rev-parse HEAD
  [repository] branch main — git rev-parse --abbrev-ref HEAD
  [repository] working tree clean — git status --porcelain
  [decisions] DEC-0001 v1 approved — [demo] Storage engine (scope: Persistence) — docs/decisions/DEC-0001-storage-engine.md
  [repository] 2 python files — file extensions
  [components] `app/` is a Python package (2 source files) — app/__init__.py
  [files] `docs/decisions/DEC-0001-storage-engine.md` matches 2 request keywords (demo, export) — keyword match: demo, export
  [files] `app/export.py` matches 1 request keyword (export) — keyword match: export (path: export)
  [files] `README.md` matches 1 request keyword (demo) — keyword match: demo
assumptions (2):
  primary language is python — basis: majority of source files
  the change most likely concerns `docs/` — basis: highest request-keyword density
unresolved questions (2):
  [dependencies] No dependency manifest found; how are dependencies managed?
  [tests] No tests found; which framework and layout should new tests use?
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase interview · complexity trivial (developer)

## 3. Interview: answer, unknown, recommendation and decision

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
$ harness interview unknown t-xxxxxxxx 3
Q3 unresolved (developer does not know)
$ harness interview next t-xxxxxxxx
Q4 [functional_behavior] (decision) How should existing behaviour change, if at all?
  recommendation needed: harness interview propose t-xxxxxxxx 4 --title ... --recommendation ... --rationale ...
$ harness interview propose t-xxxxxxxx 4 --title '[demo] Existing exports stay unchanged' --recommendation '[demo] Keep export_rows as it is and add the CSV writer next to it' --rationale '[demo] The change is additive, so no caller has to change' --impact '[demo] export_rows keeps its current behaviour'
P1 pending for Q4
$ harness interview decide t-xxxxxxxx 1 accept
P1 accepted
recorded DEC-0002 v1 -> <demo>/repo/docs/decisions/DEC-0002-demo-existing-exports-stay-unchanged.md
$ harness interview checklist t-xxxxxxxx
unresolved decisions (3):
  Q3 [functional_behavior] unanswered — What exactly should happen on the success path?
  Q5 [acceptance_criteria] not_asked — What are the acceptance criteria for calling this done?
  Q6 [acceptance_criteria] not_asked — How will each criterion be verified?
blocking: yes
$ harness interview next t-xxxxxxxx
Q5 [acceptance_criteria] What are the acceptance criteria for calling this done?

## Interrupt: the session ends here, the asked question stays unanswered

(a later session finds the task and continues where it stopped)

$ harness resume
t-xxxxxxxx · phase interview · complexity trivial · updated <timestamp>
  [demo] Add a CSV export of items
  next: harness interview next t-xxxxxxxx
$ harness resume t-xxxxxxxx
task t-xxxxxxxx · phase interview · complexity trivial (developer)
request: [demo] Add a CSV export of items
checkpoint: interview <timestamp> (#11)
Q5 [acceptance_criteria] What are the acceptance criteria for calling this done?
next: harness interview next t-xxxxxxxx
$ harness interview next t-xxxxxxxx
Q5 [acceptance_criteria] What are the acceptance criteria for calling this done?

## 4. Finish the interview

$ harness interview answer t-xxxxxxxx 5 '[demo] items.csv has one row per item'
Q5 answered
$ harness interview next t-xxxxxxxx
Q6 [acceptance_criteria] (decision) How will each criterion be verified?
  recommendation needed: harness interview propose t-xxxxxxxx 6 --title ... --recommendation ... --rationale ...
$ harness interview answer t-xxxxxxxx 6 '[demo] A unit test reads items.csv back'
Q6 answered
$ harness interview next t-xxxxxxxx
no questions left to ask; 1 open question(s) stay unresolved (see: harness interview checklist t-xxxxxxxx)
$ harness interview answer t-xxxxxxxx 3 '[demo] Write items.csv with a header row'
Q3 answered
$ harness interview checklist t-xxxxxxxx
unresolved decisions (0):
  (none)
blocking: no
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase design · complexity trivial (developer)

## 5. Architecture design with a delegated detail

$ harness design propose t-xxxxxxxx components '[demo] app/export.py gains write_csv' --rationale '[demo] Keeps all export code in one module'
E1 pending [components]
  decide: harness design decide t-xxxxxxxx 1 accept|reject|modify
$ harness design decide t-xxxxxxxx 1 accept
E1 accepted
$ harness design add t-xxxxxxxx responsibilities '[demo] write_csv writes one row per item'
E2 stated [responsibilities] (fixed)
$ harness design add --delegated t-xxxxxxxx interfaces '[demo] Exact write_csv signature'
E3 stated [interfaces] (delegated)
$ harness design none t-xxxxxxxx interactions --reason '[demo] Not needed for one CSV file'
E4 [interactions] not applicable
$ harness design none t-xxxxxxxx data_flow --reason '[demo] Not needed for one CSV file'
E5 [data_flow] not applicable
$ harness design none t-xxxxxxxx configuration --reason '[demo] Not needed for one CSV file'
E6 [configuration] not applicable
$ harness design none t-xxxxxxxx failure_handling --reason '[demo] Not needed for one CSV file'
E7 [failure_handling] not applicable
$ harness design none t-xxxxxxxx required_behaviour --reason '[demo] Not needed for one CSV file'
E8 [required_behaviour] not applicable
$ harness design coverage t-xxxxxxxx
uncovered/unresolved design items (0):
  (none)
blocking: no
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase test_plan · complexity trivial (developer)

## 6. Approval is refused before the approval phase

$ harness approve t-xxxxxxxx
harness approve: task t-xxxxxxxx is in phase test_plan; run: harness phase t-xxxxxxxx next
(exit 1)

## 7. Test plan

$ harness testplan propose t-xxxxxxxx unit '[demo] write_csv writes one row per item' --rationale '[demo] Covers the acceptance criterion directly' --covers Q5
S1 pending [unit]
  decide: harness testplan decide t-xxxxxxxx 1 accept|reject|modify
$ harness testplan decide t-xxxxxxxx 1 accept
S1 accepted
$ harness testplan review t-xxxxxxxx
test plan review (1 effective scenario(s)):
  plan not_approved
categories without scenarios: integration, e2e, regression, edge_case, failure (not required)
approval: none
blocking: yes
$ harness testplan approve t-xxxxxxxx
approved test plan (1 scenario(s), sha256:<hash>)
$ harness phase t-xxxxxxxx next
task t-xxxxxxxx · phase final_review · complexity trivial (developer)

## 8. Final design review

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

## 9. Commit the recorded decision, then Approve & Implement

$ git commit -m "[demo] record decisions"
$ harness approve t-xxxxxxxx
approved v1 -> <demo>/repo/docs/specs/t-xxxxxxxx/v1.md (revision <rev>, clean)
approval #1 recorded: spec v1 sha256:<hash> (implementation handoff authorized; AL launch is Phase 3)
$ harness show t-xxxxxxxx
task t-xxxxxxxx · phase approval · complexity trivial (developer)
request: [demo] Add a CSV export of items
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
$ harness spec verify t-xxxxxxxx
v1 ok
$ cat docs/specs/t-xxxxxxxx/v1.md
# Specification — [demo] Add a CSV export of items

- **Task:** t-xxxxxxxx
- **Status:** approved
- **Version:** 1
- **Timestamp:** <timestamp>
- **Complexity:** trivial (developer)
- **Revision:** <rev>
- **Working tree:** clean
- **Supersedes:** none
- **Content hash:** sha256:<hash>

## Requirements

- Q1 [dependencies] [demo] Standard library only, no new packages (developer)
- Q2 [tests] [demo] pytest with tests/ at the repository root (developer)
- Q3 [functional_behavior] [demo] Write items.csv with a header row (developer)
- Q4 [functional_behavior] [demo] Keep export_rows as it is and add the CSV writer next to it (accepted_recommendation)

## Architecture

### Responsibilities
- [demo] write_csv writes one row per item (fixed; developer)
### Interactions
- not applicable: [demo] Not needed for one CSV file (developer)
### Data flow
- not applicable: [demo] Not needed for one CSV file (developer)
### Failure handling
- not applicable: [demo] Not needed for one CSV file (developer)
### Required behaviour
- not applicable: [demo] Not needed for one CSV file (developer)
### Delegated to AL
- [Interfaces and contracts] [demo] Exact write_csv signature (developer)

## Components and interfaces

### Components
- [demo] app/export.py gains write_csv (fixed; accepted_recommendation)
### Interfaces and contracts
- [demo] Exact write_csv signature (delegated to AL; developer)

## Configuration

- not applicable: [demo] Not needed for one CSV file (developer)

## Acceptance criteria

- Q5 [acceptance_criteria] [demo] items.csv has one row per item (developer)
- Q6 [acceptance_criteria] [demo] A unit test reads items.csv back (developer)

## Test plan

- approved <timestamp> (1 scenario(s); sha256:<hash>)
### Unit tests
- S1 [demo] write_csv writes one row per item — covers Q5 — mock: none (accepted_recommendation)

## Rationale

- P1 [demo] Existing exports stay unchanged: [demo] The change is additive, so no caller has to change (accepted)
- E1 [Components] [demo] app/export.py gains write_csv: [demo] Keeps all export code in one module (accepted_recommendation)
- DEC-0002 v1 — [demo] Existing exports stay unchanged: [demo] The change is additive, so no caller has to change

## Constraints

- DEC-0001 v1 — [demo] Storage engine (Persistence)

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
$ harness resume t-xxxxxxxx
task t-xxxxxxxx · phase approval · complexity trivial (developer)
request: [demo] Add a CSV export of items
checkpoint: approval <timestamp> (#32)
approval: spec v1 sha256:<hash> (current)
next: harness execution start t-xxxxxxxx
```
<!-- demo-transcript:end -->
