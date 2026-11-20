# Unified task workspace interfaces

Every task opens in one ordered workflow: **Interview → Requirements → Spec →
Plan → Diagram → Review → Implementation → Checks → Delivery → History**. New
tasks open on Interview; tasks with an existing execution open on Implementation.
The question drawer stays available from every tab. The editor keeps a full
working specification separate from the approved/adopted version and current run.

## Standalone interview

From a trusted repository, start the local dashboard with
`uv run python -m harness.dashboard --project /absolute/path/to/repository`
(add `--no-browser` to print the URL without opening a browser). The interview
worker uses the existing Codex sign-in; it does not set up credentials. A host
availability result verifies an installed executable and its restricted profile,
not authentication. Authentication is verified only when the native invocation
succeeds. The current Claude client profile is not qualified for no-tool authoring,
so the UI reports it as unsupported; full manual specification editing remains
available without a native host.

For a task that names a Jira issue, the Interview tab starts with **Task
investigation**. It shows the recorded acquisition stage, selected issue/site,
bounded repository reads, source text and citations, coverage status, limitations,
and any genuine decision questions. Source findings are proposed mappings; they
do not count as developer answers. Unknown answers and deferrals remain visible
and revisitable. If the local native transport or interview model is unavailable,
the dashboard says which prerequisite failed and keeps manual answers and
specification editing usable. It never starts sign-in automatically.

After a complete source bundle exists, use **Review** to load and inspect a
candidate specification and logical graph. **Adopt this revision and request
review** uses the existing guarded reconciliation action and does not grant
approval or execution authority. Return to Interview to load the exact current
source review package, inspect the full source text, citations, candidate body,
graph, decisions, lineage and limitations, then provide an explicit written
reason and confirmation to accept source coverage. Acceptance still requires a
fresh final review and separate explicit implementation approval. If an
approved task needs new source evidence, use the existing **Return to an earlier
phase** control with a reason before refreshing sources; active or unsettled
execution remains guarded.

The dashboard adds `task.workspace = {base, source, proposals}`. `base` is a SHA-256
of the deterministic source snapshot: original request, interview statements and
unresolved checklist, accepted context, exact specification sections, design entries,
canonical diagram graph, and execution/session/epoch/generation identity. Connection
freshness, task event timestamps and check activity do not alter the binding.

`POST /api/workspace/proposals` accepts exactly `task`, `base`, `operations`, and
`request_id`; the body limit is 131072 bytes before reading. It uses the normal
Host/Origin/JSON/length guards. Request IDs are task-scoped, 8–64 alphanumeric or
hyphen characters. Creation returns 201, exact replay 200, invalid inputs 400,
unknown task/target 404, request reuse or stale source 409, and busy state 503.
Replay precedes source-staleness checks. Every successful append includes its event
in one transaction. There is no approval or acceptance route.

Each operation has `type` and `target` plus exactly these fields:

| Type | Fields | Target |
| --- | --- | --- |
| requirement | text | source requirement key (including exact spec section keys) |
| plan | text | effective E entry key |
| node_label | label | immutable source graph node ID |
| node_position | x, y | immutable source graph node ID |
| connection_add | from, to, label | unique `proposed:<ID>` |
| connection_change | from, to, label | existing base graph edge ID |
| connection_remove | none | existing base graph edge ID |
| traceability | requirement | graph node ID or effective E entry key |

Coordinates must be finite numeric values in [-10000, 10000]; bools are rejected.
The service bounds batches to 200 operations and 100000 bytes, text to 16000
characters, and labels to 500. A batch is the complete overlay relative to its
stored source. Edits to a newly added edge retain `connection_add`; deleting a
new edge removes its operation. No source node is renamed by regenerating IDs.
Saved proposals include source, operations and deterministic result; dashboard
reads add `stale` and `stale_reason`. Proposal history follows insertion order.

The client uses project/task-scoped `myharness-workspace:<project>:<task>` local
storage plus task-keyed in-memory state. Interview answers, follow-ups, wording,
deferral reasons, contribution revisions and the full specification editor use
separate authoring buffers. Dirty buffers retain their original source and base
across SSE, reload and task switches; clean buffers refresh their baseline. A save
only clears the exact version submitted, so text typed while a save is in flight
remains dirty. Conflicted and ambiguous writes retain the exact request receipt for
an idempotent retry. Storage failures leave the current page usable and are shown
to the developer.

The Interview conversation and History tab expose the saved contribution stream.
When the task snapshot is truncated, “Load earlier conversation” reads the
paginated `/api/authoring/history` endpoint and caches records by project/task;
older answers and follow-ups can be revised while their originals remain visible.
A stale-base conflict offers an explicit latest-state refresh that keeps the text
and archives the rejected request before a new save.

Source or epoch changes retain stale drafts, make text fields read-only, disable
other editor controls and disable save; the explicit discard button loads current
source. Failed saves retain an exact retry request identity; after a reload during
an in-flight save the result is shown as unknown until retry confirms it. Editing
produces a new request. Requirement/design source cards always show source text;
inspector fields and diagram overlays are visibly proposals.

The selected tab is reflected in `?tab=<name>` and survives reload. Legacy
`?workspace=design|run&tab=<name>` links are migrated; `overview` and `changes`
map to Implementation. Existing fragments remain usable: `#diagrams`, structured
`#version=...&element=...`, and `#comments` open Diagram; `#spec` opens Spec;
`#approvals` opens Review; `#execution-decisions`, `#resume` and the legacy
`#current-status` open Implementation; `#delivery` and `#history` open their
matching tabs. Clicking a different tab updates the URL and does not let an older
explicit query/hash override the new selection after reload. Task records and
historical tools remain available under History.

Model output, developer contributions and full-spec saves remain separate. The
candidate inspector loads exact specification revisions and their proposed
logical graph. Adoption requires a read-only preview, explicit graph review and
source/downstream acknowledgement, then calls the existing guarded reconciliation
route. Adoption does not approve the specification or authorize execution.
Planning controls expose interview questions and decisions, all eight design
sections (including reasoned not-applicable entries), scenario coverage and mock
justification, pending design/scenario decisions, test-plan review and approval.
The underlying Harness validators remain authoritative.

Stable UI seams for browser tests and styling:

- `#task.workspace-task`, `.workspace-top`, `.authoring-drawer`
- `.workspace-tabs [role=tab]`; `#workspace-panel[role=tabpanel]`
- tabs `workspace-tab-interview`, `-requirements`, `-spec`, `-plan`, `-diagram`,
  `-review`, `-implementation`, `-checks`, `-delivery`, `-history`
- `.workspace-inspector`, `.workspace-savebar`, `.workspace-source-card`
- `.authoring-statusbar`, `.authoring-question`, `.authoring-reconcile`, `.spec-editor`
- `.workspace-diagram-tools` provides Fit diagram, Full-size view and zoom controls;
  narrow screens can swipe the full-size canvas.
- labeled component/connection selector, Component label, Horizontal position,
  Vertical position, From component, To component, Connection label
- graph `[data-node-id]`, `.workspace-node`, `.workspace-edge`; nodes support
  Enter/F2 label editing and arrow movement; ordinary form controls duplicate
  every pointer operation
- `[data-workspace-focus]` and `[data-workspace-scroll]` preserve editor caret,
  diagram/inspector scroll and focused graph node on normal refresh
- existing approval, execution, delivery, knowledge and comments IDs/handlers are
  retained in their respective workspace tabs and disclosures

## Verification handoff

Core tests are `tests/test_workspace_proposals.py` and
`tests/test_dashboard_workspace.py`. Browser coverage includes the actual
task-start form, worker-produced interview output, answer and follow-up receipts,
full specification saves, candidate graph review and guarded adoption over HTTP,
as well as desktop/mobile light/dark layout, keyboard focus, tab-route persistence,
graph pan/fit/zoom and existing proposal/diagram compatibility. No real engineering
execution or external database is needed.
