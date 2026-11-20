# R5a actual backend contract for sequential UI integration

Actual source: R5a run c0b8fa62, writer r5a-producer-integration-002. This file describes implemented services/routes; it is not a verification/approval receipt. UI/browser work remains to be authored. Preserve existing reliability.js and all R1/R2/R3 lifecycle/focus/history fixes when adding the new journey.

## Scope and authority

Scope JSON is either `{ "task": "actual task ID" }` for cross-execution historical discovery, or `{ "task": "...", "execution": "...", "epoch": "..." }` for one actual recorded epoch. Execution and epoch must occur together. The server validates actual task/execution ownership and immutable handoff repository against DashboardServer.project_root. Action membership/evidence is checked within the selected task/repository; immutable incident facts cannot be reassigned. Explicit projection and verdict actions require the full three-field scope. Historical operator interpretation is separate from active runtime publication authority.

All new GET routes are readonly SQLite URI mode=ro/query_only, with no migration/store/index/directory repair. Provenance GET returns original accepted source metadata; it does NOT reopen/repair source files or upgrade the original acceptance into current verification. Missing DB returns503 without creating its parent. Old R1 `/api/execution/incidents` and `/detail` retain existing shapes/IDs and remain available.

All POSTs use the existing dashboard header boundary: exact allowed Host and same-origin Origin, JSON content type, exactly one Content-Length, no transfer encoding, allowlisted route. Reliability body cap32768 bytes is enforced BEFORE reading any body. Local attribution is `local_dashboard_operator`, not a named authenticated person. No native tool exposes these actions. They grant no runtime/deployment/approval authority.

## GET routes

All accept task and optionally execution+epoch. Duplicate/unknown query keys refuse400. All responses include schema_version1, scope and a coverage object.

1. `/api/reliability/incidents`
   - Optional category,source_kind,q (literal substring <=200),limit1..100(default25),before_time+before_id paired.
   - Items: incident_id,schema_version1,source_kind,task_id,execution_id,epoch_id,category,classification_version1,created_at,symptom_code,provenance,indexing (`indexed|legacy_unindexed`).
   - Descending stable `(created_at,incident_id)` keyset; no100-row traversal ceiling.
   - `next_cursor` is null or `{created_at,incident_id}`. Translate these two keys into query `before_time,before_id` on the next page. Do not merge a partial newest page into an older contiguous window; preserve the R1 Latest/Older window behavior.
   - Canonical facts UNION currently unindexed R1 facts, exactly once. GET never indexes.
2. `/api/reliability/detail?task=...&incident=...`
   - `item`: canonical fact above.
   - `source`: closed provenance descriptor below.
   - `investigation`: current revision record or revision0 with initial document. Facts and operator interpretations are separate.
3. `/api/reliability/investigation?task=...&incident=...`
   - Optional limit1..100,before_revision.
   - `incident`, descending revision `items`, `next_revision` or null. To fetch older revisions, use before_revision=next_revision.
4. `/api/reliability/groups`
   - Lists existing persisted suggestions; GET does not generate groups.
   - Optional limit1..100,before_time+before_id. `items`, `next_cursor` is null or `{before_time,before_id}` (already query-shaped).
5. `/api/reliability/group?task=...&group=...`
   - Optional limit,before_revision for decision history.
   - `group`: current suggestion/decision; `history`: `{items,next_revision}` immutable decisions.
6. `/api/reliability/projection`
   - Requires full task/execution/epoch tuple.
   - `checkpoint_revision`, `unindexed_observed`; coverage is an as-of read, never a permanent completion claim.

## Source metadata

Supported actual adapters: operation_dispatch, al_test_result, al_review_finding, contract_rejection, budget_decision, operator_verdict. R7 evaluation source and R5b standalone provenance remain explicitly pending/separate.

`source` has source_kind and a fixed `reference`:
- operation: legacy_incident_id,observation_id,source_id (actual audit ID).
- test/review: event_id,item_id,source_revision; plus `accepted_source` containing actual kind/id/sha256 of the accepted immutable receipt/report.
- test also intended_purpose,remote_runner_provenance (`not established by intended purpose`),captured_validity,latest_observed_validity. This is latest persisted semantic observation, not a new Git/candidate verification performed by GET. A failed check does not prove root cause/incorrect implementation.
- contract: rejection_id,observation_id(nullable),boundary(dispatch|assignment_wait),contract_family(nullable),error_code,fixed schema-root field_pointer. No rejected body/value/prose/hash.
- budget: policy_decision_id,attempt_id(nullable). No guessed pool/dimension or measurement. Unsupported enforcement is not exhaustion.
- operator: operator_request_id,verdict_revision; plus separately attributed `operator_report` with actual target,reason,evidence,actor_kind. Raw deliverable bytes are never copied into incident facts.

Use actual IDs/closed kinds for navigation. Do not render raw paths or invent report/import authority from the decision-only receipt. Existing execution trace navigation may use each item's actual task/execution/epoch; no trace is reconstructed if unavailable.

## Investigation documents/revisions

Revision record: revision_id,revision,parent_revision_id,actor_kind,request_id,created_at,document. Initial view has revision0,revision_id null. Source fact/classification is immutable.

Document fields: schema_version1,incident_id,facts:[incident IDs],classification:{category,classification_version,provenance},hypotheses,affected_components,controls,corrective_actions,corrections,status,rationale.

Hypothesis: `{cause,confidence,supporting,contradicting,operator_explanation?}`. Confidence is ordinal unassessed|low|medium|high, never a calibrated probability. References are actual incidents in selected scope, including cross-execution evidence under task scope. Nonunknown causes need supporting evidence. Cause vocabulary: unknown,incorrect_implementation,architecture_violation,specification_drift,unauthorized_operation,repeated_unsuccessful_edits,invalid_handoff,hallucinated_dependency,hallucinated_api,incorrect_tool,missing_context,excessive_retries,budget_exhaustion,failed_verification,human_rejected_deliverable.

Controls: `{kind:authority|dispatch|contract|budget|verification|review,state:held|failed|unknown,evidence:[incident IDs]}`. Known states need evidence. Actual authority/typed-contract/budget refusal defaults held. Generic DISPATCH_REJECTED defaults UNKNOWN: an arbitrary ValueError cannot prove a safety control held or that partial effects did not occur. Test/review failures do not automatically become failed safety controls.

Components:
- `{reference:ordinary repository-relative path,verification:"unverified"}` is an authored claim.
- Verified reference requires `{reference,verification:"verified",source_version,snapshot}` matching an actual unpurged Knowledge source/version/snapshot membership under the configured repository. This verifies the recorded historical reference, NOT current mutable source stability, security, causal impact or native qualification. Existing Knowledge evidence is reused; no second memory store/gate. UI must label this basis explicitly rather than imply verified root cause.

Corrective actions: a code or `{kind:code,operator_explanation?}`. Codes investigate,add_regression,review_contract,review_policy,review_context,review_design,evaluate_change. These are suggestions, not commands.

Corrections: `{field:classification|hypotheses|affected_components|controls|status,interpretation:closed cause/control code,rationale,operator_explanation?}`. Cumulative append; old revisions/facts remain visible.

Rationale: `{code,evidence:[incident IDs],operator_explanation?}`. Codes evidence_updated,interpretation_corrected,evidence_sufficient,not_reproduced,duplicate_symptom,unrelated_symptoms,scope_changed. Resolved/dismissed requires newly supplied rationale+nonempty evidence. Resolution is an attributed investigation decision, not deployment/verification/approval.

Operator explanations are optional, specific, separately authored strings <=2000 characters. Existing credential recognition rejects credential-bearing text before persistence; this is not a guarantee arbitrary authored text contains no unknown secret. Automatic metadata stays closed. Unknown fields, fact/classification replacement and caller-provided authority/approved flags refuse.

## POST routes and exact bodies

Common `{schema_version:1,scope:<scope>}` plus the fields below. All successful replies200: `{schema_version:1,scope,result,authority:"local_dashboard_operator; no execution or approval grant"}`. No native principal argument is accepted.

- `/api/reliability/investigate`: incident,expected_revision,request_id,changes.
  - Changes may contain hypotheses,affected_components,controls,corrective_actions,corrections,status,rationale only.
  - Exact normalized request replay before revision comparison. Changed reuse or stale revision409. Legacy-unindexed target must first be explicitly projected (no hidden historical write).
- `/api/reliability/suggest`: limit (1..100),cursor (null or internal `{created_at,incident_id}`).
  - Explicit action on one bounded incident window, not global exhaustive similarity.
  - The API validates actual per-item configured repository scope outside the append transaction; the service compares the same window inside its transaction, refusing a changed window.
  - Result schema_version,scope,items,next_cursor,legacy_unindexed_in_window,coverage. Use result.next_cursor for the next explicit suggestion window. Legacy rows are skipped until explicitly indexed.
- `/api/reliability/group-decision`: group,expected_revision,request_id,decision.
  - Decision `{kind:accept|reject,rationale}`, or `{kind:split,partitions:[[IDs],...],rationale}`, or `{kind:supersede,target_group,rationale}`.
  - Split partitions every member exactly once; supersession target is a real same-scope group, never self. Append-only attribution; no merge/delete of incidents.
- `/api/reliability/project`: expected_revision,request_id,limit (1..100); full scope required.
  - Result `{schema_version:1,projected_count,checkpoint_revision,more_as_of_commit,coverage}`.
  - Selects currently unindexed legacy rowids from the beginning, never a timestamp watermark. Old exact replay returns original receipt and consumes no late rows. New request after an empty batch may discover late/backdated old-pin records.
- `/api/reliability/verdict`: request_id,target,reason,evidence; full scope required.
  - target exactly `{artifact_id,sha256}` for an actual published native-agent-output in the selected recorded epoch's server-owned operations/drafts store.
  - reason is rationale above; target itself supplies observed deliverable provenance, so additional evidence may be empty.
  - Actual bounded immutable buffer resolves outside transaction; source kind/ID/digest must match index. No arbitrary path/body importer. Original request replay is durable; conflicting reuse409. verdict_revision1 creates one operator_reported human_rejected_deliverable incident, even for historical execution context, without changing execution status/epochs/approvals.
  - Current finite target coverage is published native-agent-output, not arbitrary authored drafts, uploads, remote delivery statuses or standalone AL exports.

## Group shape and UI state

Group: group_id,algorithm_version1,safe_key,members:[incident IDs],member_metadata:{id:{actual task/execution/epoch/source/category/code/tool/role/policy/spec}},created_at,revision,decision(null or recorded operator decision),meaning:"related symptoms; common cause not established".

Closed boundary_rejected symptom family relates actual authority/dispatch/typed-contract refusals; other codes remain separate families. Unknown metadata is null. Version/source differences remain explicit. Same exact input/algorithm/key returns the same suggestion and durable reject/split/supersede decision. New evidence/window creates a distinct inspectable input set; it never erases old decisions or establishes a shared cause.

Builder must implement actual existing-dashboard search/detail/provenance, hypothesis/correction/status/history controls, group accept/reject/split/supersede, projection and verdict journeys. Preserve independent list/detail/history/edit request state, stale response/task guards, contiguous paged history, bounded rendered rows with unbounded traversal, focus/selection/collapse behavior and honest conflict reload. Do not restart every in-flight request during polling. Display facts, accepted findings, operator reports and hypotheses distinctly. Keep all proposals/controls non-authorizing. Verify desktop/narrow actual screenshots, including offscreen action/group/revision sections, for focus, wrapping, overflow, alignment and UX alongside functionality.

## Error boundary

GET400 invalid query;404 missing/foreign/unavailable scoped record;503 storage unavailable. POST409 invalid action/supporting evidence or request/revision conflict (fixed safe messages);404 scope/record missing;503 storage unavailable. Header refusal403/415/411/400 and bodycap413 occur before body reads. Never display storage exception prose, raw rejected data, payload hashes or a fictitious named-human/approval claim.

## Accepted limits to preserve during support

- The current q predicate searches safe canonical incident ID/category/symptom-code/source-kind fields. Do not label it an arbitrary report/log/full-text search. Source reference IDs remain inspectable in detail; no raw report importer exists.
- HELD describes the specific evidenced check's refusal. It proves neither the absence of earlier effects nor process/resource settlement. UNKNOWN is deliberate for generic dispatch exceptions.
- Root resolved the future-schema lead internally: keep tested0023 unchanged, with a named actual R7 evaluation-result adapter/schema obligation. Do not create a dummy evaluation source/FK, widen the current CHECK or fabricate a measured result. R5b standalone work is separately source-designed behind this same facade and must retain its actual AL domain without synthetic Harness execution/task/epoch IDs. None of that future work is claimed implemented here.


## Published output discovery for the verdict journey (final service addition)

GET `/api/reliability/outputs?task=<task>&execution=<execution>&epoch=<recorded epoch>&limit=25` requires all three scope fields. Optional `before_id=artifact-<32 lowercase hex>`; max limit100. It returns the standard schema_version/scope/coverage envelope plus `items`, `next_cursor` (null or `{before_id}`), `source_available`, `content_revalidated:false`, `ordering:"artifact_id descending"`, and `metadata_limit_bytes:16777216`.

Each descriptor is exactly artifact_id,sha256,name,owner,role,size,graph_revision for an actually indexed native-agent-output. sha256 retains the ArtifactStore `sha256:` prefix. Select a descriptor and POST its `{artifact_id,sha256}` as verdict.target with the SAME full scope. The verdict performs its own exact bounded content validation before accepting it. This is descriptor inspection, not content/current-source revalidation. Artifact-ID ordering makes no newest-time claim. Traverse all pages with the returned before_id; rendered list may stay bounded. New rows during traversal are not a frozen snapshot; explicit reload can discover arrivals.

The list reads the selected recorded epoch's server-owned operations/drafts index using existing regular nofollow/nonblocking descriptors and existing shared lock. It neither opens output contents nor creates database/store/directory/lock/index, performs repair, or returns filesystem paths. Index reading is bounded16MiB; malformed/busy/unavailable storage gets fixed503. A missing directory/lock/index gives200 with source_available:false and no items, without recreating it. Foreign scope404, missing full tuple400. HTTP tests cover actual public submit_output→descriptor selection→historical verdict, missing-store filesystem nonmutation and105 published descriptors traversed20/page.

Existing `/api/execution?task=...&execution=...` also exposes current selected epoch `reports[].{artifact_id,sha256,name,owner,role,size,resource}`, but it is not the bounded historical descriptor service. Use the new endpoint for the historical verdict selector.
