# AL execution event contract v1

Status: implemented candidate protocol, version 1. The harness owns approval,
the immutable execution handoff, developer decisions and presentation. AL owns
task planning, agent allocation, admission, evidence and guarded completion.
Events describe committed AL state; they do not grant workflow authority.

The same validator and examples ship in both platform packages at
`al/{codex,claude}/skills/al/reference/`. The executable source is
`harness/execution_contract.py` and each installed AL package includes its own
`al_execution_contract.py`, `al_events.py`, `al_execution_tasks.py` and
`al_execution_decisions.py`. These are mirrored implementations; the package
does not import the harness.

## Binding and envelope

An event has exactly `schema_version`, `event_id`, `sequence`, `timestamp`,
`binding`, `kind` and `payload`. Version is the integer `1`; event IDs and
entity IDs are stable ASCII identifiers; sequence begins at 1 and increases by
one. The binding fixes the harness task and execution, approval ID, approved
spec version and content hash, source revision and review-state hash, handoff
hash, AL session and AL run. Native conversation identity is persisted
separately by the host adapter. The JSON schemas and canonical example are
published next to this document.

Records are limited to 262,144 bytes and nested values, strings and collections
are bounded. Duplicate JSON keys, non-finite numbers, unsupported schema
versions, malformed payloads, foreign bindings, sequence gaps and conflicting
replays are rejected. Identical replays are idempotent. An incomplete trailing
JSONL line is not an event and does not advance the cursor.

## Event families

The v1 kinds are `run.started`, `run.ended`, `phase.changed`,
`agent.registered`, `agent.updated`, `task.registered`, `task.updated`,
`dependency.changed`, `ownership.changed`, `handoff.created`,
`blocker.raised`, `blocker.resolved`, `decision.acknowledged`, `test.started`,
`test.result`, `writer.updated`, `implementation.result`, `amendment.created`,
`amendment.revalidated` and `delivery.updated`. Delivery events define a future
contract only; this release does not connect GitLab, pipelines or deployment.

Agent and task status is one of Idle, Running, Waiting, Blocked, Failed or
Completed. Registration describes an actual invocation, not a planned role.
Progress is null (unknown), measured `{completed,total,unit}`, or an explicitly
labelled estimate `{value,source,label}`. A 100% report is descriptive and does
not complete a task. Duration and narrative never create progress.

Task records include stable IDs, criteria, repository-relative owned paths and
dependencies. Graph changes are validated for known identities, cycles and
ownership. Handoffs name existing tasks. A blocker identifies its direct tasks;
AL computes the dependent closure and prevents admission only for that closure.
The whole AL run remains active while independent work continues.

## Authority and persistence

AL writes the event outbox in the same atomic fsynced state replacement as the
transition it describes. Canonical outbox history is retained; the read-only
exporter pages at most 200 events and 512 KiB per response. The harness commits
raw events, normalized projections and the cursor in one SQLite transaction.
Rejected protocol records are visible in diagnostics and do not skip the
expected sequence.

Only authoritative AL transition handlers emit phase, test validity and
implementation results. `implementation.result` is successful only when the
existing AL approval handler has produced its guarded review, task hash, fresh
and comparison review, evidence and fingerprint receipt. Host exit, prose,
progress reporting and a semantic event from an unbound source cannot complete
the run. The supervisor reporting interface cannot submit authoritative result
events.

For a task decision, the harness first records the explicit developer choice in
the existing Decision Registry and publishes one immutable execution-bound
delivery. AL checks its exact run, approval, blocker revision, decision hash
and task scope, then commits acknowledgement with the corresponding state
change. Rejection remains blocking. An amendment preserves the original
approved package, affects only its validated scope and requires scoped Architect
revalidation before affected admission resumes. These decision events do not
alter the pre-execution approved-spec reconstruction.

The event schema is descriptive; it does not authorize filesystem paths,
commands, host selection, approval changes or evidence overrides. Harness HTTP
routes use fixed operations and execution-bound identifiers. Source freshness
is reported separately from browser/SSE connectivity.

## CLI and resources

Installed AL runtimes expose read-only `integration capabilities` and
`integration events --after N --limit N`, plus guarded `integration` task,
agent, blocker, decision and revalidation commands. The exact command forms and
authority requirements are in each package's `reference/execution-integration.md`.

The harness can start only from the current exact approval and current approved
test plan. Claude is the default native host; Codex is explicitly selectable.
Authentication or capability errors remain visible, and the harness never
silently changes host. See [the approved integration decision](decisions/DEC-0019-native-cli-al-integration.md)
and [the capability audit](phase-3-al-capability-audit.md).
