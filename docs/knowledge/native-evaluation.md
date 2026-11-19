# Native evaluation infrastructure and recovery limits

`python scripts/knowledge_native_eval.py CASE.json` defaults to validation and spends no
provider tokens. `--mode prepare` creates an isolated SQLite store, concrete interview,
design, test plan, final review and ordinary immutable approval. `--mode live` additionally
reserves an execution and invokes its real supervisor. Live runs are explicit and supervised;
this increment's tests use synthetic identities/providers only.

## Current recovery boundary (2026-11-19)

The private recovery implementation disables transcript-derived observation. Both
`observe_return()` and `observe_usage()` refuse with the fixed error
`qualified native observer unavailable`; the legacy `records()` entry point also
refuses without opening host session files. No qualified replacement observer is
installed. Public scalar telemetry is reported metadata, not proof of role identity,
child return, clean-session isolation, settlement or budget compliance.

Evaluator donor completion refuses before authority updates, report ingestion or a
completion receipt. Recipient admission also refuses legacy donor receipts; record
hashes cannot upgrade them. Ordinary authorized Harness non-review demonstrations
are a separate route and remain unexecuted in this recovery. Refusal does not
satisfy K5, K7 or K8. All K1–K9 acceptance obligations remain open.

Current receipt kinds are:

| Kind | Meaning |
| --- | --- |
| `public-provider-telemetry-v2` | Closed supported scalar projection, or a fixed unavailable diagnostic; no raw event/hash. |
| `native-return-observation-v2` | Explicit unavailable child-return observation. |
| `native-generation-usage-v2` | Unavailable scoped generation counts; null deltas/answer tokens and `UNKNOWN` answer-token compliance. |
| `evaluation-worker-failure-v2` | Execution ID, numeric worker return code, fixed diagnostic and unknown settlement; worker streams are discarded. |

Source/build/package and deliberately authored output hashes remain valid integrity
bindings. They do not restore lost approval or observation authority. The original
R13 source and observer/review proposal bodies were lost; their surviving hashes
cannot recover their contents or transfer authority. See [recovery status](recovery-20261119.md).

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
implement continuity transfer. The historical stage contract below describes the now-refused evaluator continuity flow. Authenticated report output is available; completed donor lifecycle, supervisor
ingestion into a later task, portable lesson approval and source/destination grants remain
separate genuine obligations. No fake writer or SQL grant is created. Donor and recipient
costs must be reported separately and combined when those demonstrations are performed.

The runner does not read gold, score answers, choose case order, tune retrieval or claim the
20% threshold. `scripts/knowledge_eval.py` remains the offline accounting consumer; do not
feed unknown backend read counts into its integer fields as zero. Preserve the approved
agent-visible versus backend and donor/recipient sensitivity interpretations. Actual provider
and held-out execution, sourced-quality scoring, clean-session proof, full budget verification,
and business acceptance remain supervising-agent work after release.

## Historical evaluator continuity contract and trusted task adoption

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

The earlier implementation attempted to finalize donors from canonical session
metadata, child-terminal records and a matching root wait. That observation route
has been removed. Current evaluator donor finalization always refuses before any
`Idle → Waiting → Completed` updates, ingestion or completion receipt. Both B and C
remain refused; no replacement observation token is issued.

Historical recipient wrappers referenced donor admission/completion artifact hashes
and the same project/task/store. These files remain historical. Current
`donor_capability()` validates basic existing bindings and then refuses before
opening the donor database. Its former positive identity, settlement, AL-source,
report/owner/role/task and artifact validation path was removed as unreachable.
Any future re-enabling must explicitly restore and qualify the complete genuine
bindings; deleting the fixed refusal alone is insufficient.

The stage-wrapper example above is retained only as schema history. Do not run a
live donor/recipient evaluation until an approved safe observer has been qualified.
Trusted task adoption is distinct from evaluator donor continuity.

Historically, recipient-stage scope was added through ordinary interview/design/test-plan/final-review and
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

## Prospective M2 method

Schema version 1 packages and their original counters/receipts remain legacy V1. Do not
retroactively attach reserves, corrected counters or usage evidence to them, pool cohorts,
or erase failed attempts. Schema version 2 requires an exact `method` object from
`scripts.knowledge_evaluation.method.contract()`: method ID `knowledge-native-m2-r2`,
capability profile, reserve values, generic instruction hash and shared product tool-help/schema
hash. Unknown versions, unsupported declarations, altered totals and mixed-method donor
capabilities refuse before admission. The explicit stage wrapper stays version 1 and retains
the nested package's method; M2 donor/recipient participants require their matching stage.
M2 declares the Codex Architect/Astra/medium and
Engineer/Sol/medium profiles. Provider qualification remains a separate supervisor obligation.

M2 keeps the totals at 30 tools/call attempts, 98,304 delivered bytes and 240 seconds. The
1,800 answer tokens remain a request. Inside the totals it reserves 16,384 completion bytes,
including a 512-byte error allowance, and the last three calls for transition/refusal, fresh
assignment and one output; donors reserve a fourth call for root completion. Initial root and
child assignment calls count normally. Data or listing responses that exceed the evidence
allowance are replaced whole by a fixed actionable refusal. Retrieval then stays closed;
only fresh assignment → output → donor completion (where applicable) is admitted. An unused
refusal slot is not extra capacity. Repeated invalid attempts can still consume the remaining
calls; oversized control responses record control-budget-exhausted. No truncated JSON,
extra call, invented revision, retry submission or timeout grace is supplied.

The shared adapter lock protects a participant ledger across all connections. Every
well-formed JSON-RPC tools/call envelope with object params counts, including unknown tools,
invalid arguments, pre-dispatch authentication failure, and over-limit attempts (31 is recorded
as 31, never clipped). Malformed JSON-RPC/envelopes, pre-session HTTP rejection and HTTP bearer
failures are transport diagnostics outside this participant-call seam; they are not authenticated
agent operations or evidence of a passed call budget. Original MCP initialization, verifier,
claim and OperationController authority ordering remains intact. `forwarded_requests` counts
requests forwarded into that original handler, `admitted_requests` counts successfully presented
results, and `denied_requests` counts error results. Thus admitted + denied equals attempted;
forwarding alone establishes neither authenticated authority nor successful delivery.

Tools/list requests are counted separately; every filtered listing's full serialized UTF-8
metadata counts against the same byte total. For tools/call, each outgoing text block's UTF-8
bytes count exactly once, including pre-dispatch errors and control acknowledgments. MCP/HTTP
framing, initial fixed instructions and native collaboration/context are outside that byte seam.
When the error allowance or total is exhausted, an empty error carries zero text bytes and the
ledger preserves exhaustion. Listing refusal retains a valid empty tools list with a bounded
`_meta.evaluation_error` notice; its serialized metadata is charged. Once the error
allowance is gone, the empty tools-list framing contains no tool metadata or text and is
unmeasured, just like an empty tools/call error frame. Internal guarded registration/finalization controller operations
are counted separately and never represented as participant requests. Whole context bytes,
backend reads and unique source bytes remain unavailable; do not coerce null into zero for
`knowledge_eval.py`'s integer-only accounting.

M2 records `output_committed` and `acknowledgment_delivered` separately. Before acknowledgment
budgeting, it reconciles the actual owning execution's immutable index, owner, role, current
report graph revision, expected hash, file identity and streaming content hash. It never
interprets or persists answer bodies for reconciliation and never calls submit_output again.
A failure after publication can therefore be stored-but-unacknowledged rather than falsely
classified absent. A stale graph or forged owner is refused. The exact report reference is
preserved; reconciliation does not invent child return, completion or settlement. Donor
completion is currently unavailable before lifecycle mutation or ingestion. No
completion receipt is emitted. Stored output, acknowledgment, child return and
settlement remain separate facts; output reconciliation supplies none of the
missing observation authority.

Product MCP descriptions now document the semantic query, explanation, confined read,
assignment and output contracts. This is a new truthful product-help improvement, not recovery
of richer text previously stripped by the evaluator. M2 preserves the selected bridge's allowed
schemas/descriptions verbatim and supplies the same generic instructions in B/C. It does not
restore unrestricted worker prompts, harness resources, tools or permissions. Isolation receipts
bind evaluator hashes; selected bridge/module hashes remain independently recorded.

Scoped root/child generation usage is currently unavailable. No host session
records, record positions or record hashes are read or retained by the evaluator.
Generation deltas and answer tokens remain null; answer-token compliance remains
`UNKNOWN`. Closed public host usage may be reported when supported, but it is not
qualified per-child measurement. Cached input is within input, reasoning output is
within output, and generation output may include tool arguments. Neither bytes
nor generation counts establish exact answer.md token counts.

There is no arbitrary `private/**` policy. Closed M2 capability declarations cannot invent one.
Public canary tests characterize a benign tracked `private/canary.pem` outside decision/report
resources across ordinary baseline readers and cold commit/working-tree Knowledge refresh/query.
They explicitly allow filename inventory visibility and demonstrate ordinary `.md` text in the
same directory remains readable. Exact registry history and donor reports are independent
source kinds; an authorized authored report can deliberately contain disclosed information.
No universal content secrecy or no-existence-leak boundary across those resources is claimed.
Independent qualification must cover the exact placement and every advertised reader/history/
report path before using an integrity fixture. Unsupported boundaries remain unavailable;
this increment changes no source scope, index, extraction, ranking, grant or permission.

Method tests use public disposable repositories and synthetic host metadata/executables through
real admission, selected bridges, worker reservations, NativeRuntime, operation authority and
cleanup. They establish method contracts, not provider quality or mission acceptance. The failed
original cohort remains failed historical evidence. Independent public native qualification,
method freeze, fresh independently authored cohort, unchanged scoring, root regression and
mandatory AL review/acceptance remain supervisor-owned.

Acknowledgment delivery is measured at the returned `MCPConnection.request` content seam;
client consumption and socket receipt are unobserved. A budgeted acknowledgment is not a
native acknowledgment of understanding. M2 records explicit unavailable child-return observations and actual exited-attempt/closed-context teardown facts separately.
Evaluator file hashes are checked against isolation.json before dispatch and again after the
worker returns; the actual generic prompt hash and selected bridge hashes remain separate.

The current public telemetry projector retains only enumerated event fields,
supported model/effort values, constrained identities, and bounded nonnegative
finite counts/timings. Booleans, nested unknown usage, unsupported models and
malformed values yield a fixed unavailable diagnostic without rejected values or
event hashes. This scalar projection is not a qualified lifecycle observation.
V1 evaluator observation and continuity are also refused; no legacy receipt is upgraded. M2's exact generic budget/terminal-retrieval/no-retry
policy is included in the literal designated Child instructions payload for every stage,
with identical B/C wording. Donor finalization remains a root-only stage directive outside
that payload. V1 prompt bytes remain unchanged; corrected M2 prompt/source hashes require
new independent public qualification before any prospective private evaluation.


Worker interpreter source selection is explicitly bound for future V1 and M2 runs.
The selected evaluator namespace precedes ambient editable/package paths; the
separately authenticated Harness bridge remains first for Harness imports.
The parent records evaluator hashes in isolation.json, and the explicit worker
bootstrap rechecks those hashes and rejects foreign preimported evaluator modules
before dispatch. Native receipts record the actual evaluator module paths/hashes.
This does not upgrade or reinterpret historical V1/M1 receipts.


M2-r2 uses capability profile `codex-readonly-ordinary-text-exclusions-v2`. Its ordinary
source transport supports Makefiles, extensionless scripts and arbitrary safe text suffixes
in the exact frozen tracked corpus. Both execution-file protected paths and Knowledge
protected names/directories apply. Descriptor-relative no-follow, single-link, bounded
stable UTF-8, NUL and secret checks screen the entire file before paging or hashing.
Engineer task-file path validation uses the same combined exclusions; tracked membership
and automatic dependency-setup refusal remain required. Knowledge indexing eligibility,
extraction and ranking are unchanged. There is no caller-controlled bypass.

Old M2-r1 contract objects refuse before admission under this implementation, including
direct adapter construction and task-scope validation. They must use their original frozen
engine to retain the old interpretation. Explicit schema-version-1 source reading and
scopes retain the index-format restriction. R2 requires fresh independent public
qualification and a new method freeze before private execution; unit tests do not supply it.


## Native diagnostic retention

Native launch/supervision diagnostics now use a closed code and authored-message
catalog. Recognized provider failures retain actionable usage-limit,
authentication-required, permission-denied or generic-provider-failure codes.
Classification of known prose markers is transient and occurs only inside an
already-recognized failure envelope. Ordinary prose never grants failure or success.
Provider prose, reset details, exception text, native stderr, rejected values and
whole-event/stream hashes are not retained on these new native diagnostic paths.
Native stderr is drained with at most one fixed suppression observation per attempt;
stderr alone does not change the result. Oversized/incomplete framing remains a
failure. Unmanaged worker stdout/stderr goes to `DEVNULL`.

The exact `Startup failed before native spawn:` prefix remains available with a
fixed stage code. Authentic no-spawn/closure evidence still governs retry safety;
a message or missing PID cannot make an unknown/native-recorded attempt eligible.
Usage-limit failure stays failure even if the host exits zero or subsequently says
`success`. Zero exit without a guarded AL implementation result remains incomplete.
Historical evidence is retained unchanged. These changes qualify no provider or
observer and do not alter native identity, authentication selection or permissions.
