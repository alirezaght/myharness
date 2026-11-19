# Knowledge workflow context

These additions use the existing authenticated tools and local Knowledge store.
They provide observations and format feedback; they grant no execution, approval,
completion or writer authority. Full Knowledge K1–K9 acceptance remains separate.

## Assignment observation

`await_assignment` retains its existing Supervisor, ReadOnly, Assigned and Pending
behavior. `registered_assignment` describes only the caller's recorded agent and
AL task: agent ID/status, AL task ID/status, `task_revision`, files and graph
revision, plus the distinct outer `harness_task_id`, execution ID and epoch ID.
Its status is `available`, `not_registered` or `unavailable`. Missing registration
never falls back to a task mentioned in a prompt. `task_revision` is observational;
it is not the writer admission revision or permission to modify those files.
The response also describes `knowledge_contribution_contract`.

## Operation receipts and immutable context

Successful `knowledge_query` responses include `receipt_id` for that operation's
persisted receipt. Identical queries can return the same `bundle_hash` with
different receipts. `knowledge_explain` returns its own new receipt and the
explained bundle hash. Investigation/checkpoint consumers use the returned ID,
not the latest receipt for a matching hash. Failed publication returns no
successful receipt reference.

`receipt_id` and `bundle_hash` are transport metadata outside the immutable payload.
The payload hash is SHA256 of its canonical JSON. The byte budget reserves the
fixed receipt presentation overhead. Existing two-argument `ContextBundle`
construction remains supported without a receipt field.

## Evidence derivations

New assertions include `derivation_parents`: full assertion IDs discovered and
validated during parent closure. These fields participate in byte fitting and the
immutable payload hash. They are evidence dependencies, separate from architecture
traversal `paths`.

Explanations return `derivation` with direct parent IDs, child-to-parent edges,
parent assertions and their citations from the authorized bundle. Status is
`complete`, `incomplete`, or `unavailable-budget`. Every exposed endpoint belongs
to that bundle and remains subject to current scope, purge, revocation and evidence
checks. Legacy bundles use actual stored relationships within their bundle;
unavailable closure never becomes an invented complete empty graph. Legacy bytes
and hashes are not rewritten. Explanation responses remain bounded by the bundle's
response budget, at most 32 KiB; optional excerpts may be truncated to fit.

## Contribution feedback

`submit_output` publishes the exact named report first, then returns advisory
`knowledge_contribution` feedback for those bytes:

```json
{"contract_version":1,"status":"ready","issues":[],"truncated":false}
```

Statuses are `ready`, `invalid`, `not_applicable` (an empty assertion collection),
or `unavailable` when validation cannot run. Issues contain only closed codes,
optional numeric assertion/parent indexes and allowlisted field identifiers.
Rejected values, arbitrary field names, report prose and exception messages are
never echoed. The same parser serves completed-report ingestion.

Assertion keys are `subject`, `predicate`, `object`, `value`, `text`, `parents`.
Subject, predicate and text are strings. Parents are exact full assertion IDs,
not source locators, prefixes or prose. Narrative classifications such as STATIC
belong in authored text; per-assertion `status`, `origin` or `verification` fields
are not accepted. Origin and verification are server-assigned attributed inference
and unverified status.

Existing narrative forms remain supported: plain text, supported JSON summaries,
answers, findings, observations and sourced facts. Existing extraction caps are
40 assertions, 40 parents per assertion and 8192 text characters. Assertion/text
extraction truncation is explicit in feedback; excessive parent count is invalid.

Format readiness establishes neither completed-producer provenance nor current
parent eligibility, source visibility or final ingestion. Invalid reports remain
available as originally authored. Submit a separately named corrected report;
the supervisor chooses whether and when to complete the role. Optional Knowledge
ingestion failure does not reverse otherwise authorized AL completion. Final
publication still enforces existing completed producer, latest output, role,
graph revision, source and scope guards.

The prior invalid pilot report and all historical failures remain evidence.
A subsequent native demonstration requires a new scoped AL task/invocation;
these interfaces do not permit replacement-provenance or lifecycle bypasses.
