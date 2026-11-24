---
name: harness
description: Continue Harness planning in the current provider session and keep the browser dashboard connected to this same conversation.
---

# Harness shared-session workflow

Use the already-running provider session. Do not delegate this task to a subagent,
start another model, use a native session-resume API, or inspect private transcript
storage. The browser is a second surface for the same task; it is not a second agent.

## Start or attach

When the user gives a new request, preserve the exact request and any exact
requirements or answers they have already provided in this same conversation.
Do not invent answers or approvals. If a task already exists, first read its
canonical task context with:

```sh
harness session status --repo "$PWD" --task TASK_ID --context
```

Use `--db PATH` before `session` when the project uses a non-default database.
If this task is already attached to this same healthy caller, continue its current
epoch. If it is a task without an owner, use the supported explicit attach flow.
Do not replace a different healthy owner. Takeover is only an explicit user
decision and requires `--takeover`.

For a new task, create the bounded start request JSON with schema version 1,
the canonical project identity, a fresh request ID, the user's exact request and
the selected complexity, then run `harness session start`. The returned task ID,
attachment ID, epoch, dashboard URL/command and capability path are the receipt.
Keep capability paths private; never put capability content in task files,
browser requests, publications, logs or answers.
Harness creates the external capability directory and owner-only file. If you
deliberately create a new directory first, use mode `0700`; capability files
must remain mode `0600`. Never read or copy capability contents, and never
change permissions broadly on an existing directory.

After start or attach, launch or reuse the local dashboard using the exact
`dashboard_command` in the receipt and open its `dashboard_path`. Keep the
dashboard server owned until the user pauses or the task is complete. Its shell
process is separate from the one foreground provider wait.
Retain the full dashboard URL printed by that launch or reuse command. The
receipt's `dashboard_path` is a relative task route, not an origin. Combine that
path with the retained dashboard origin; never guess a port or infer one from a
process ID. Keep the task ID from that receipt:

```sh
DASHBOARD_ORIGIN='<origin from the exact full dashboard URL printed by the command>'
DASHBOARD_PATH='<dashboard_path from the receipt, beginning with />'
DASHBOARD_URL="${DASHBOARD_ORIGIN}${DASHBOARD_PATH}"
TASK_ID='<task ID from the receipt>'
```

After start or attach, run `session prepare-interview` once in the attached
foreground context for an ordinary request, then read status with `--context`.
For a task explicitly targeting a Jira issue, do not call `prepare-interview`:
use the currently authorized read-only Jira tool and the supported repository
observation/publication path. Existing task answers and requirements are
canonical context; do not begin a duplicate interview merely because the
dashboard opened.

## Closed request shapes

Get the project hash without importing Harness from a system Python or opening
the database:

```sh
harness session project --repo "$PWD"
```

For a new task, write this exact shape to a temporary request file, substituting
the returned project hash and fresh values:

```json
{"schema_version":1,"project":"<project-hash>","request_id":"<fresh-request-id>","request":"<exact user request>","complexity":"<selected complexity>"}
```

Then run `harness session start --repo "$PWD" --provider codex|claude
--file REQUEST.json`. For an existing task, `session status --context` supplies
the current semantic base. An explicit same-caller reattach request has this
shape and uses a new request ID:

```json
{"schema_version":1,"project":"<project-hash>","task":"<task-id>","request_id":"<fresh-request-id>","expected":{"authoring":<current-authoring-base>,"input_revision":<current-input-revision>},"previous_epoch":<current-epoch>}
```

Only reattach after the same caller's attached lease is expired, uncertain or
disconnected, and only while the original task authorization continues. Run
`harness session attach --repo "$PWD" --provider codex|claude --file ATTACH.json`
without `--takeover` and without passing capability contents on the command
line; Harness creates or locates the owner-only capability file. The receipt
prints its owner-only file path for subsequent commands in this same session.
Do not reattach over a healthy different caller. Use `--takeover` only after an
explicit user decision to replace a healthy owner.

After a lease expires or continuity is uncertain, first read `session status`
and current task context. If the original authorization still applies, this is
the same provider caller, the owner is not a different healthy caller, and the
user has not explicitly paused the session, use the supported same-caller
reattach flow above. An explicit pause ends the loop; wait for the user to
resume before reattaching. `SESSION_OWNER_LOST` or a different healthy owner is
not a reattach signal.

All owner operations use the following exact common fields:

```json
{"schema_version":1,"project":"<project-hash>","task":"<task-id>","attachment_id":"<attachment-uuid>","epoch":<current-epoch>,"request_id":"<fresh-request-id>","expected":{"authoring":<current-authoring-base>,"input_revision":<current-input-revision>}}
```

Use that object unchanged as the base for `prepare-interview`,
`observe-repository`, and `wait`. To acknowledge a delivered batch, add exactly
`delivery_id` and `event_ids` to a fresh copy; acknowledgement means received
only. To publish, add `consumes` (the ordered event IDs being answered) and one
`content` object. Closed content variants are:

- `{"task_message":{"text":"..."}}`
- `{"progress":{"stage":"...","text":"..."}}`
- `{"candidate":{"body":"## ...","graph":{...}}}`
- `{"question_proposal":{"concern":"<catalogue concern>","text":"...","rationale":"...","citations":[],"supersedes":null,"changed_premise_reason":null}}`
- `{"user_response":{"target":"question-response:<id>","target_revision":"<current target revision>","mode":"answer|unknown|defer","text":"<exact user wording>"}}`
- `{"user_response":{"target":"recommendation:<id>","target_revision":"<current target revision>","mode":"accepted|rejected|modified","text":"<exact user wording>"}}`
- `{"source_bundle":{"attached_envelope":{"schema_version":3,"origin":"attached","task":"<task-id>","project":"<project-hash>","attachment_id":"<attachment-uuid>","epoch":<current-epoch>,"selection":{...},"sources":[...],"evidence":{...},"observations":[...],"limitations":[...],"captured_base":<current-owner-request-expected>,"candidate":{"body":"## ...","graph":{...}}}}}`

For a deferred question response, include its validated `source` and `ref` fields
inside `user_response`. Use the exact selected source/revision identities from
the current canonical context; never fill placeholders by guessing. Ticket
selection is `{ "issue": "KEY-123", "site": {"id":"...","url":"https://…atlassian.net"} }`;
repository-only selection is `{ "schema_version": 1, "kind": "repository" }`.
The source bundle still needs the complete validated source/evidence envelope;
the shorthand above is not permission to omit citations or provenance.

For each owner operation, write the exact common request fields above and use a
fresh request ID. Run `harness session prepare-interview` or
`harness session observe-repository` with that file, provider and the receipt's
capability-file path. For delivery, run
`harness session wait --timeout 25`; if the shell reports a running handle,
continue polling that exact handle. Acknowledge only the returned IDs with
`harness session ack` and its `delivery_id`/`event_ids` fields. Publish the
response with `harness session publish`, the ordered `consumes` IDs and one
closed `content` variant. Use a fresh request ID for each new operation; exact
transport retries reuse the same file and ID.

On explicit pause, stop the foreground wait loop and do not reattach
automatically. To detach or change modes from the TUI, use an owner request file and
`harness session detach --destination manual|standalone`. Choose standalone only
after an explicit user request; this changes the mode and does not mean a model
was started. The browser's “Pause shared session” action records a pause signal;
its “Save a reconnect request” action does not impersonate the invoking agent.

## Investigate and publish

Use only the invoking agent's already-authorized repository and provider tools.
For Jira, use the task's configured read-only access and report operational gaps
plainly. Do not create or edit Jira content, request new scopes, or start login.
Run the bounded `session observe-repository` operation when repository evidence
is needed. Keep exact citations, source identity, limits and source hashes in the
typed source bundle.

When a delivered event has kind `workspace.proposed`, do not acknowledge or
consume it using only its proposal ID. Resolve that exact ID from the known
dashboard origin before responding:

```sh
curl --fail --silent --show-error --get \
  --data-urlencode "task=$TASK_ID" \
  "$DASHBOARD_ORIGIN/api/dashboard"
```

In the response, select `task.workspace.proposals[]` by the exact ID in the
event's nested proposal payload. Verify that `task_id` matches the receipt,
`base` matches the event base, and `stale` is false for the current task/project.
Inspect the complete `operations`, captured `source`, and
`result` to understand the proposed edits. Bound local output to the selected
record; this endpoint lists all task proposals and currently has no pagination.
If the exact record cannot be retrieved, is stale, or is too large to inspect,
leave the event unconsumed and do not publish that the proposal was handled.
Publish a non-consuming progress message describing the read gap, then retry
the read. Never ask the user to repeat the diagram edits. Reading or
acknowledging a proposal does not adopt it; source review and explicit adoption
remain separate actions.

Publish progress, cited source observations, proposed specification and graph,
and genuine unresolved decision questions through the closed `session publish`
content variants. Treat every candidate as a proposal. Keep user answers in the
user's exact words, bound to the displayed question or proposal revision. Never
publish a fabricated answer, approval, delivery acknowledgement or execution
request.

Explain briefly that the user can answer in either the browser or this TUI. The
browser saves its own answer immediately; the agent must not ask the user to
repeat that answer in the TUI.

## Foreground delivery loop

While this task remains active, keep one foreground `harness session wait`
operation running with a timeout no greater than 25 seconds. If the tool reports
that its command is still running, poll that same tool operation until it returns
events or times out. A running-process handle is not a timeout. The whole
foreground tool call must return after this one bounded wait; do not wrap several
waits in one shell loop. After a timeout, return to the model and issue the next
bounded wait as a separate tool call. Never start a second concurrent wait or a
background polling process.

For returned events:

1. Inspect the exact event IDs, task, epoch and current semantic base.
2. For every `workspace.proposed` event, first resolve and inspect its exact
   proposal record as described above. Do not acknowledge that event until the
   complete record is available and understood.
3. Record receipt with `session ack` only for the delivered event IDs. Receipt is
   not application and is not an approval.
4. Re-read the current canonical task context as needed and respond through
   `session publish`, consuming the appropriate ordered event IDs.
5. Confirm the committed publication result before treating the browser input as
   applied. The dashboard distinguishes saved, received and applied states.
6. Continue the foreground loop while the user is considering a question. Do not
   end each turn with “tell me to check the browser.”

A normal message from the user in the TUI can interrupt the wait. Preserve and
publish that exact wording through the same attached session before continuing.
If the TUI says a user message is queued behind a tool, use that provider's
normal send-now or interrupt control; Claude's TUI provides `Ctrl+X` then
`Ctrl+S`. The Harness CLI cannot inject a native TUI message.
On explicit pause, use the supported detach action. On ownership loss, expired
lease, uncertain clock, unavailable clock, unsupported protocol or changed
healthy owner, read status and follow the specific recovery rules above. For
`SESSION_CLOCK_UNAVAILABLE`, a `permission_denied` reason means ask the provider
to approve the same foreground Harness command using its normal permission
flow, then retry the unchanged request file, request ID, repo/database target,
provider identity and capability. A failed fresh start retries `start`; it has
no admitted task to attach to. Other reasons require resolving that host
prerequisite and do not promise permission approval will fix it. Clock
unavailability never authorizes reattach, takeover or mode switching.

Before a lengthy bounded repository or ticket investigation, publish a concise
truthful progress event through the current attached session. Continue in
bounded steps and return to the foreground wait loop; do not add a background
heartbeat or model process.

## Review and completion

Keep the full specification and logical diagram editable in the dashboard. Make
source acceptance, candidate adoption, material-input reconciliation, design
review, test-plan approval and final approval distinct explicit actions. Explain
what is waiting and what the next safe action is. Do not describe received work
as applied or proposed work as adopted.

Use `harness session status --context` for current task state. Do not claim that
the package being installed qualifies native provider behavior; only an actual
same-session provider demonstration can establish that.

## Implementation through AL

After Approve & Implement, Harness supervises the implementation and drives AL with its intent commands: `verify` runs the missing evidence and the base and candidate gates, then enters verification and review or names the exact blocker; `writer finish --id <id> --outcome success --evidence auto` finishes a writer with the checks its changes need; `next` shows the next step (and any answered agent question to forward to the same warm agent), and `next --do` runs it when it is a safe deterministic operation. The fine-grained AL commands are for exceptions and recovery only. Checks on inputs that already passed are reused from AL's action cache: the dashboard and the completion report show each result as `ran now` or `reused from run <run> on the same input tree <tree>`; relay that provenance and never rerun a check only to make it look fresh. The dashboard also shows each AL agent's progress and open questions live; when the developer answers one there, Harness delivers it to the agent that asked, so do not answer agents' questions for the developer. The approved test plan reaches AL as check recipes, one evidence area per category (`testplan-<category>`).
