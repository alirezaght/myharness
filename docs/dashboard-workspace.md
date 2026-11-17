# Plan & Design workspace interfaces

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

The client uses `myharness-workspace:<task>` local storage and an in-memory map.
Drafts retain their original source and base across SSE, reload and task switches.
Source or epoch changes retain stale drafts, make text fields read-only, disable
other editor controls and disable save; the explicit discard button loads current
source. Failed saves retain an exact retry request identity; after a reload during
an in-flight save the result is shown as unknown until retry confirms it. Editing
produces a new request. Requirement/design source cards always show source text;
inspector fields and diagram overlays are visibly proposals.

The URL accepts `?workspace=design|run&tab=<name>`. Existing fragment links remain
usable: `#diagrams`, structured `#version=...&element=...`, and `#comments` open
Plan & Design → Diagram; `#spec` opens Requirements; `#approvals`,
`#execution-decisions` and `#resume` open Live Run → Overview; `#delivery` and
`#history` open their matching Live Run tabs. Task records and historical tools
remain in a disclosure on History, and raw source IDs/hashes stay in secondary
details on the requirements inspector.

Stable UI seams for browser tests and styling:

- `#task.workspace-task`, `.workspace-switch` (Plan & Design / Live Run)
- `.workspace-tabs [role=tab]`; `#workspace-panel[role=tabpanel]`
- tabs `workspace-tab-requirements`, `-plan`, `-diagram`, `-review`, `-overview`,
  `-changes`, `-checks`, `-delivery`, `-history`
- `.workspace-inspector`, `.workspace-savebar`, `.workspace-source-card`
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
`tests/test_dashboard_workspace.py`. Builder must adapt historical migration-count
assertions in `tests/test_db.py` for 0017, adapt long-page browser journeys to the
new navigation, and verify desktop/mobile light/dark visuals, graph pointer and
keyboard routes, saved overlays after reload, add→edit/remove connection behavior,
SSE during input/drag, task isolation, late save responses, stale bases and retry.
No real engineering execution or external database is needed.
