# Qualified native evaluation infrastructure

`python scripts/knowledge_native_eval.py CASE.json` defaults to validation and spends no
provider tokens. `--mode prepare` creates an isolated SQLite store, concrete interview,
design, test plan, final review and ordinary immutable approval. `--mode live` additionally
reserves an execution and invokes its real supervisor. Live runs are explicit and supervised;
this increment's tests use synthetic identities/providers only.

A case is a closed JSON object with these fields:

- `schema_version`: `1`; `case_id`; `arm`: `B` or `C`; `participant`: `single`, `donor` or
  `recipient`; `prompt`; `host`: `codex` or `claude`; actual specialized `role` (`al-architect`,
  `al-designer`, `al-scout`, `al-engineer`); exact installed role `model` and `effort`.
- `corpus`: `root`, exact Git `head`, `tree`, `files_sha256`, `file_count`.
  `files_sha256` hashes canonical sorted compact UTF-8 JSON mapping every tracked pathname
  to its SHA256. It must come from the independently frozen corpus receipt. Computing a
  receipt from a candidate and calling it frozen does not establish baseline provenance.
- `allowed_roots`: exactly the single corpus root. `state_directory` and `output_directory`
  must be absent, canonical, disjoint from each other, the corpus and evaluator checkout;
  their parent directories must exist. Use separate physical corpus/state/output per arm.
- `budgets`: `delivered_bytes: 98304`, `tool_calls: 30`, `wall_seconds: 240`,
  `answer_tokens: 1800`, with exactly those integer values.
- `provenance`: `accepted_mission_sha256`, `evaluation_authorization_sha256` and `statement`
  containing exactly:

> This task operationalizes the previously approved S1–S7/Q30 evaluation scope. Approval is recorded by the supervising agent under that existing authorization; the user did not separately review or click approval for this generated specification.

- `specification`: `request`, `answers` mapping exact interview question text to concrete
  supplied answers, `design` mapping all eight `DesignSection` values to relevant design,
  and `scenarios`, each with `category` and `text`. Investigation-generated questions also
  need concrete answers. Missing answers or a blocked review refuse approval.
  Engineer cases additionally require `specification.task_files`: an explicit nonempty
  list of existing frozen tracked source files/directories. Protected/secret/unknown scopes
  and any scope that would trigger automatic dependency preparation are refused. The same
  concrete scope must be used in both arms. The supervisor
  prepares these inputs from the accepted evaluation authorization, without gold labels.

Preparation is single attempt and does not resume. A failed preparation leaves its existing
state intact. A separately approved new attempt needs a new package and roots; never silently
retry, delete authority, edit sealed policies or replace an old runtime pin. The ordinary
Harness approval record is retained alongside explicit supervisor provenance; it is not a
new user click. `prepare` is not a native execution and cannot later be silently reused as one.

## Private scheduling and tool adapter

The live launcher starts isolated Python (`-I -B`) with the authenticated selected bridge
first on its import path. All imported Harness module paths/hashes are checked and recorded.
Only one public `launch_worker` call is intercepted at `_spawn_worker`; its real WorkerOwned
attempt/token is inspected, interception is restored, and unchanged `supervise` runs after
release of the launch lock. The token is never generated or inserted by evaluator code and
is never written into evaluation reports. There is no detached second worker, retry, reconnect,
resume, recovery transition, production authority edit or pin bypass. The original supervisor
owns initialization, actual native observation, heartbeat, cleanup and attempt settlement.
A crash after reservation remains an unresolved actual reservation. Missing PIDs do not prove
settlement. Exited/Interrupted is retained and must not be called AL-completed.

A process-local MCP connection/list wrapper reuses the original HTTP server, bearer checks,
closed schemas, native principal resolver and OperationController authority. It advertises
only permitted tools, and separately denies all other dispatch. B never indexes its isolated
store and denies knowledge calls, alternative Harness/artifact resources, automatic assignment
context, contributions, shell, mutation, refresh and unrestricted AL commands. C adds only
unchanged authenticated `knowledge_query`/`knowledge_explain` for the exact specialized role.
The real native role profile must match the configured model and effort; no fallback is used.

Identical evaluator-only `read_file` resources in both arms are `evaluation:analyze` (original
pure analyzer including relevance), `evaluation:audit` (original routing/ownership audit), and
`evaluation:decisions` (original DecisionRegistry current/history readers), plus
`evaluation:suggest` (original decision suggestions confined to the same corpus). Source
reads are available only to the actual specialized role; root source prefetch is refused. Ordinary source
reads are confined to tracked frozen paths with descriptor-relative secret/alias checks.
The first authenticated root invocation must call `await_assignment` before spawning a single
fresh specialized role. Its first authenticated request registers that observed child through
ordinary artifact/agent-register operations using the observed supervisor principal. This
necessary registration is private adapter plumbing, not model-selected arbitrary AL access.
The child submits exactly one `answer.md` via real authenticated `submit_output`.

Engineer retains its actual writable-capable native identity; no role is relabelled.
Its explicit source scope is included in the ordinary approved design, then a genuine
AL evaluation task and the observed Engineer are registered under actual approved criteria.
No task is admitted/started and no writer is created. Native assignment is therefore
reported honestly as `Assigned` with unavailable handoff and unavailable knowledge context.
The adapter refuses an active writer or available context/handoff and never triggers the
underlying automatic context path. Scopes that could trigger `auto_prepare` are rejected
before registration. Engineer reads, C queries and named output use its unchanged real
principal; writable potential grants no model-visible write operation.

C cold indexing uses a task-neutral immutable commit refresh before native question delivery;
it records cost separately and does not index the evaluator checkout, admission database or
other-arm outputs. Incomplete coverage must be interpreted from actual refresh outcomes.

## Evidence and limits

The adapter counts UTF-8 serialized tool results after its presentation boundary, including
listing metadata and repeated delivery. It refuses over-budget delivery rather than claiming
bytes are tokens. Native tool/error events, actual output artifact references, approval and
execution/epoch/AL bindings, adapter source hashes, bridge/module hashes and host public result
usage fields are retained. Raw provider streams, hidden reasoning and transport tokens are
not copied. Wall expiration raises through unchanged supervisor cleanup. Process crashes and
infrastructure refusals remain unavailable/partial results with no recorded retry.

Backend logical source reads, unique span coverage, actual per-child provider model/effort,
no-inheritance provenance and answer token enforcement are not fully observable at these
seams. Their fields stay unavailable/null. The 1800-token request is a native instruction,
not an enforced token ceiling. Host result usage is retained where present; it must not be
misrepresented as per-child usage. The evaluator's fixed native/role instructions and host
conversation framing are not fully measured by the tool-result byte counter. Consequently
these receipts alone cannot establish all-context budget compliance or frozen evaluation
acceptance. They establish only declared adapter observations subject to these limits.

In the single-case route, `participant` labels preserve donor/recipient separation but do not
implement continuity transfer. The explicit stage route below adds the bounded task-local flow. Authenticated report output is available; completed donor lifecycle, supervisor
ingestion into a later task, portable lesson approval and source/destination grants remain
separate genuine obligations. No fake writer or SQL grant is created. Donor and recipient
costs must be reported separately and combined when those demonstrations are performed.

The runner does not read gold, score answers, choose case order, tune retrieval or claim the
20% threshold. `scripts/knowledge_eval.py` remains the offline accounting consumer; do not
feed unknown backend read counts into its integer fields as zero. Preserve the approved
agent-visible versus backend and donor/recipient sensitivity interpretations. Actual provider
and held-out execution, sourced-quality scoring, clean-session proof, full budget verification,
and business acceptance remain supervising-agent work after release.

## Explicit continuity and trusted task adoption

The original `--route single` remains the default and refuses existing state/output roots.
Use `--route stage` only with a closed stage wrapper:

```json
{
  "schema_version": 1,
  "action": "donor",
  "package": "<the complete case object described above, not a filename>",
  "al_task_id": "evaluation-donor",
  "task_files": ["harness"],
  "existing": null,
  "donor": null
}
```

`package` is the actual object. All stages need explicit real tracked source scope, including
Architect donors. Engineer scope must also match `specification.task_files`. Donor stage uses
fresh absent roots. Each stage has its own fixed budgets, fresh output directory and native
conversation. A named output alone does not finalize the producer.

The donor root actually waits for its child, then calls `read_file` with exactly
`evaluation:complete-donor`. Only its genuine authenticated root can finalize the one observed
child and exact report/current graph. The private Codex observer checks its own canonical
SessionMeta parent/role, TurnContext/turn ID, one task_started/task_complete pair and a matched
root wait_agent call/result after completion. Wait result text is ignored; timed_out=false
alone grants nothing. Metadata reads are bounded regular, own, nofollow/no-alias reads;
reasoning, last_agent_message and native prose are not retained. Missing/partial/resumed or
foreign terminal metadata refuses. Claude terminal observation remains unavailable.

After actual return, ordinary `Idle → Waiting → Completed` records a returned invocation
awaiting supervised finalization and then its finalized report. It does not assert an admitted,
started or verified AL task. C uses normal completed-report ingestion. B's declared private
hook refuses at ingestion entry, before the original body or any knowledge mutation; ordinary
lifecycle/output persistence still runs and the unavailable contribution outcome is retained.
The actual worker must subsequently close and settle before recipient admission.

Recipient wrapper uses `action: recipient`, a distinct `al_task_id`, a new absent output root,
and the same physical case-arm repository/state/database/Harness task. Its `existing` object is:

- `database`, `specs`, `task_id`: exact canonical state-bound existing store/spec root/task.
- `task_request_sha256`: SHA256 of the actual immutable task request UTF-8 bytes.
- `investigation_sha256`: SHA256 of canonical sorted compact JSON of the actual latest
  investigation checkpoint payload, which must bind the exact repository.
- `approval_id`, `approval_content_hash`: actual current donor approval identity/hash.
- `knowledge_binding`: null for continuity; exact operator-prepared public demo bindings below.

`donor` contains `admission` and `completion`, each `{ "path": "<canonical receipt>",
"sha256": "<exact hex SHA256>" }`, referencing the actual donor `admission.json` and
`producer-completion.json`. Actual native Exited/closed-context, original AL source hash,
completed report/owner/role/task/artifact hash and original project/task/store/provenance are
revalidated. No DB is copied and no previous authority is made current.

Recipient-stage scope is added through ordinary interview/design/test-plan/final-review and
new sealed approval/version under existing mission authorization. A new execution consumes
that new approval. Historical approvals and original donor outbox/handoff remain retained.
Authorized fixture transitions occur externally only after actual donor settlement; the next
stage's exact corpus receipt must describe the real resulting Git identity. C uses a normal
task-neutral working-tree incremental refresh, retaining temporal reports while preserving
real working-tree provenance/limitations. A pure explicit commit refresh intentionally excludes
mutable reports and is not silently overridden.

The recipient may explicitly read `evaluation:donor-report` through the same baseline resource
in B and C. It resolves only that exact capability-bound donor report and supports ordinary
bounded offset/limit pages. It does not list prior outputs, read another task/arm or inject donor
prose into the recipient prompt. C additionally has normal authenticated knowledge tools.
Every delivered report page counts again toward the recipient context budget; donor cost is
separate and combined cost remains a required accounting interpretation.

`action: adopt` supports a trusted operator-prepared read-only project/task **before its first
implementation approval**. Supply the same explicit existing-task binding, with null approval
fields, no donor reference and no previous task execution. Normal public workflow creates its
first approval; no SQL approval, task ID assumption or made-up user click is accepted. B
refuses seeded knowledge. C may retain pre-staged data only with an explicit `knowledge_binding`
containing `project_id`, `grant_id`, `material_sha256`, matching the actual unrevoked exact
material/destination grant. Actual lesson export/approval/grant/import/revoke remain production
public operations; this route grants nothing and does not replace exact human authorization.

Stage validation is read-only. `--mode prepare` stops after ordinary approval and is not a
resumable provider attempt. `--mode live` is explicit and parent-only for actual provider runs.
Unavailable telemetry, ingestion refusals, incomplete coverage and Interrupted execution status
remain literal outcomes. Mixed commit/report labeling is unchanged product behavior, not an
excuse to suppress mutable report provenance/hash validation or claim clean-review capability.
