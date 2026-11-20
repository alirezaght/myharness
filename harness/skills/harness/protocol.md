## Session protocol reference

All JSON request files are bounded data files created for the current operation.
They contain no capability secret. Use exact keys shown here; unknown fields are
refused. Use a fresh UUID request ID for a new operation and preserve the exact
file/request ID only when retrying that operation after an unknown transport
outcome.

### Start, attach and owner requests

Get the canonical project identity with `harness session project --repo PATH`.
Start request:

```json
{"schema_version":1,"project":"<64-hex-project-hash>","request_id":"<uuid>","request":"<exact user request>","complexity":"trivial|simple|standard|complex"}
```

Run `harness session start --repo PATH --provider codex|claude --file FILE`.
The receipt includes the task, attachment UUID, epoch, current base, dashboard
command, dashboard path and owner-only capability file path.
Harness creates the capability directory and file. If deliberately creating a
new directory first, use mode `0700`; capability files are mode `0600`. Never
read or copy capability contents or broadly chmod an existing directory.
Keep repository, request, capability and database paths absolute. `--repo`
selects the project; it does not change the shell working directory or resolve
a relative `--file` against that repository. Absolute request paths continue to
name the same bytes if a tool changes its working directory.

Same-caller explicit reattach request:

```json
{"schema_version":1,"project":"<project-hash>","task":"<task-id>","request_id":"<uuid>","expected":{"authoring":<current-authoring-base>,"input_revision":<current-input-revision>},"previous_epoch":<current-epoch>}
```

Run `harness session attach --repo PATH --provider codex|claude --file FILE`.
Use `--takeover` only after explicit user authorization to replace a healthy
owner. A missing or changed owner, healthy different owner, or missing original
caller context is not permission to take over.

The common owner request for `prepare-interview`, `observe-repository`, `wait`,
`ack`, `publish`, and `detach` is:

```json
{"schema_version":1,"project":"<project-hash>","task":"<task-id>","attachment_id":"<attachment-uuid>","epoch":<positive-epoch>,"request_id":"<uuid>","expected":{"authoring":<current-authoring-base>,"input_revision":<current-input-revision>}}
```

Use the latest base returned by `session status --context` or the last committed
operation. `ack` adds exactly `delivery_id` and `event_ids`. `publish` adds
`consumes` and exactly one `content` variant. Run `wait` in the original
foreground provider conversation with `--timeout 25`; keep polling the same
running shell operation until it returns. `wait` returns ordered events plus a
delivery ID; ack means received, not applied.

Every owner CLI command except `status` and `project` also needs `--provider` and `--file`;
`wait`, `ack`, `publish`, and `detach` require the receipt's `--capability PATH`.
Pass only the capability file path, never its contents. The command must run in
the current provider context so Harness can verify its real caller identity.
Each foreground `session wait --timeout 25` must be the only wait in that tool
call. Poll the same running command handle if the shell reports it is still
running, then return to the model before issuing another bounded wait. Do not
wrap multiple waits in a long shell loop or start a background waiter. If a TUI
message is queued behind a tool, use the provider's normal send-now/interrupt
control (Claude: `Ctrl+X`, then `Ctrl+S`); the CLI cannot inject native TUI input.

### Reading a proposed workspace edit

The `workspace.proposed` event identifies a stored proposal at
`event.payload.payload.id`. Before acknowledging or consuming that event, read
the task's current dashboard view from the exact local origin retained from the
launch/reuse receipt:

```sh
TASK_ID='<task ID from the receipt>'
DASHBOARD_ORIGIN='<origin from the exact full dashboard URL printed by launch/reuse>'
DASHBOARD_PATH='<dashboard_path from the receipt, beginning with />'
DASHBOARD_URL="${DASHBOARD_ORIGIN}${DASHBOARD_PATH}"
curl --fail --silent --show-error --get \
  --data-urlencode "task=$TASK_ID" \
  "$DASHBOARD_ORIGIN/api/dashboard"
```

Select `response.task.workspace.proposals[]` by that exact ID and verify the
record's `task_id` equals the receipt task ID, its `base` equals the event base,
and `stale` is false against the current task/project. Inspect its complete
`operations`, captured `source`, and
`result` before deciding how to respond. The endpoint currently returns all
proposals without pagination; filter locally to the one ID and treat an
unretrievable or uninspectably large proposal as an operational read gap.
Leave that event unconsumed, publish only non-consuming progress about the read
gap, and retry the read. Do not ask the user to repeat the edits or claim they
were handled. Reading or acknowledging this record never adopts it; acceptance
and adoption are separate explicit dashboard actions.

### Closed publication variants

`content` must contain exactly one variant:

```json
{"task_message":{"text":"..."}}
{"progress":{"stage":"...","text":"..."}}
{"candidate":{"body":"## Proposed specification\n...","graph":{"nodes":[...],"edges":[...]}}}
{"question_proposal":{"concern":"<known concern key>","text":"...","rationale":"...","citations":[...],"supersedes":null,"changed_premise_reason":null}}
{"user_response":{"target":"question-response:<id>","target_revision":"<current target revision>","mode":"answer|unknown|defer","text":"<exact user wording>"}}
{"user_response":{"target":"recommendation:<id>","target_revision":"<current target revision>","mode":"accepted|rejected|modified","text":"<exact user wording>"}}
{"source_bundle":{"attached_envelope":{...}}
```

For `user_response` with mode `defer`, include the exact additional fields
`source` and `ref`. Answer and deferral never mean approval. Keep proposal,
receipt and committed application states separate.

### Attached source bundle

`attached_envelope` has exactly these keys:

```json
{"schema_version":3,"origin":"attached","task":"<task-id>","project":"<project-hash>","attachment_id":"<attachment-uuid>","epoch":<current-epoch>,"selection":{...},"sources":[...],"evidence":{...},"observations":[...],"limitations":[...],"captured_base":<same-owner-request-expected>,"candidate":{"body":"## ...","graph":{...}}}
```

For a Jira task, `selection` is exactly
`{"issue":"KEY-123","site":{"id":"<verified-site-id>","url":"https://<site>.atlassian.net"}}`.
Use only the exact issue and verified site from the current authorized
read-only Jira context. For repository-only evidence, selection is exactly
`{"schema_version":1,"kind":"repository"}`. `observe-repository` supplies
bounded repository sources, hashes, limitations and managed-artifact policy;
retain those records unchanged.

Each source has exactly `id`, `kind`, `locator`, `content`, `sha256`, and
`metadata`. `kind` is `ticket` or `repository`. SHA-256 is over UTF-8 source
content. To construct a ticket source, preserve the ticket locator/content and
metadata, then calculate identity from canonical JSON:

```text
metadata = the exact metadata object ({} when none)
source_hash = SHA256(UTF8(content)).hexdigest()
identity_input = {"locator": locator, "content": content, "metadata": metadata}
canonical = JSON(identity_input, keys sorted, UTF-8, no whitespace, non-ASCII preserved)
id = kind + ":" + SHA256(UTF8(canonical)).hexdigest()[0:24]
```

Do not alter a source record returned by the repository operation. Keep ticket
text bounded; do not publish credentials or private configuration.

`evidence` has exactly `coverage`, `decisions`, and `reads`. For a complete
attached publication, `reads` is `[]`. Each coverage row has exactly:

```json
{"key":"<known concern key>","disposition":"fact|interpretation|not_applicable|decision|operational_gap|conflict","statement":"...","rationale":"...","citations":[...]}
```

Each decision row has exactly:

```json
{"concern":"<same key as a decision coverage row>","text":"<genuine unresolved developer choice>","citations":[...],"supersedes":null,"reason":"..."}
```

Do not present an inferred answer as a decision. Only add a decision row when
the same concern's coverage disposition is `decision`. `supersedes`, when
present, is the positive ID of an actual earlier decision question.

A citation has exactly `source`, `sha256`, `start`, `end`, and `quote`. Start
and end are zero-based Python string character offsets into the exact stored
source content; `quote` must equal `content[start:end]`, and the SHA must equal
that source's SHA. Cite between one and eight exact ranges per coverage or
decision row. An uncited clarification may use the known concern key with an
empty citation list only after ordinary `prepare-interview` has established the
required investigation checkpoint; it must have `supersedes:null` and
`changed_premise_reason:null`.

`observe-repository` returns confined source records but does not publish an
active source bundle. Before any cited question or proposal, publish the
validated `source_bundle`, then cite only exact source records from the current
active bundle. An uncited genuine clarification is allowed only after ordinary
`prepare-interview` established the investigation checkpoint; use the known
concern key, `citations:[]`, and the exact required null fields. Do not cite an
observation that has not been published as an active bundle.

The complete candidate graph has exactly `nodes` and `edges`: 2–80 nodes and
1–160 edges. Each node has exactly `id`, `label`, `kind`, `status`, `sources`,
and `assumption`; each edge has those keys plus `from` and `to`. IDs are unique
stable identifiers. Node kinds are `actor`, `component`, `data`, `interface`;
edge kind is `relationship`. Status is `proposed` or `assumption` only. Every
edge endpoint names a node, every source reference resolves to `request`, a
current question/design reference, or `investigation:<task-id>:<source-id>`, and
the graph includes at least one `component` or `interface` node. Agent content
cannot label anything accepted or completed.

### Dashboard and recovery actions

The `session-action` HTTP endpoint accepts exactly
`{"task":"<task-id>","request_id":"<uuid>","expected":<session-base>,"epoch":<epoch>,"action":"pause|request-reattach|choose-standalone|choose-manual"}`.
This is an explicit dashboard action. A pause stops the attached loop; do not
reattach automatically after the user pauses. A reconnect request is a queued
task event for the original agent, not a browser-side attach. Standalone/manual
choice changes task mode only and never starts a model itself.

### Host clock recovery

`SESSION_CLOCK_UNAVAILABLE` means Harness could not obtain a validated host
boot identity; it is distinct from `SESSION_CLOCK_UNCERTAIN`, which means clock
continuity could not be established from an available observation. Status may
include `state:"clock_unavailable"` and
`clockUnavailable:{operation:"read_host_boot_identity",reason,permissionRetry}`.
The reason is one of `permission_denied`, `unsupported_platform`,
`invalid_observation`, or `read_failed`; no boot token is exposed.

For `permission_denied`, ask the provider to use its normal explicit permission
flow for the same foreground Harness command. Retry the exact same request file
and ID, repository/database target, provider/caller identity and capability
artifact. A fresh failed start retries `start`, not `attach`. Do not grant
permission automatically, change host configuration, request blanket access,
create a helper, synthesize identity, take over, reattach or switch modes.
Other reasons require resolving the reported prerequisite; permission approval
is not promised to fix them. An unavailable observation grants no ownership or
lease authority.

After an expired lease or uncertain continuity, status must be read first. A
new epoch may be attached only by the same still-authorized caller using the
explicit attach request above, when no different healthy caller owns the task
and the user did not explicitly pause it. Explicit pause waits for user
resumption. Ownership loss and a healthy different owner are stop conditions.
