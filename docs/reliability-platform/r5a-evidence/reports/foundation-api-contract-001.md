R5a foundation internal integration contract — session reliability-r5a-20261120 / run c0b8fa62

This is an internal Python/SQLite contract, NOT a public HTTP API or publication-authority receipt.
All selected scopes are exactly {task,execution,epoch}; existing query.scope validates actual task/execution/epoch.

Migration0023 is additive/schema-only. Old R1 and eight-column policy_decisions are unchanged. Catalog facts and typed source relations are immutable. Generated reverse deferred FKs require exactly the matching relation at transaction COMMIT; relation→catalog composite FKs prevent a second wrong family. Source/task-binding triggers prevent mismatched identities. An integration publisher must insert the source and relation within its existing owning transaction, never in a nested writer.

catalog.project_operation(conn, incident_id): requires caller transaction; SAVEPOINT guards catalog+source insert even if caller catches a source exception. Reads actual immutable R1 fact, keeps exact incident ID/category/code/time, idempotent exact projection. Does not create source observations or confer historical runtime authority.
catalog.record_budget_decision(conn, decision_id): requires caller transaction; only existing deny/BUDGET_EXHAUSTED rows; exact policy source, idempotent; NULL attempt preserved. No dimension/pool inferred. Calling from actual policy.audit with current epoch/owning connection remains NEXT stage.
catalog._insert is PRIVATE low-level machinery; never expose it as arbitrary source importer. Event/contract/operator publication adapters remain NEXT stage and must validate real producer provenance before using the schema.

catalog.list_incidents(conn, selected, limit=25,cursor=None,category=None,source_kind=None,q=''):
- readonly canonical UNION ALL R1 rows lacking operation relation; no projection.
- limit1..100; literal q <=200; descending(created_at,incident_id).
- returns schema_version1,scope,items,next_cursor; cursor exactly {created_at,incident_id} or null.
- items: incident_id,schema_version,source_kind,task_id,execution_id,epoch_id,category,classification_version,created_at,symptom_code,provenance,indexing(indexed|legacy_unindexed).
catalog.detail(conn,selected,incident): same item, scope-checked; missing → LookupError.
source_queries.source_relation(conn,selected,incident): {source_kind,reference}; fixed safe keys by family:
operation legacy_incident_id/observation_id/source_id; budget policy_decision_id/attempt_id(nullable); event event_id/item_id/source_revision; contract rejection_id/observation_id(nullable); operator operator_request_id/verdict_revision. No report/path/log resolution.

legacy_projection.pending(conn,selected): readonly first101 currently unindexed legacy incident IDs by rowid, not a timestamp watermark.
legacy_projection.project_legacy_batch(conn,selected,expected_revision,request_id,limit=100): owns BEGIN IMMEDIATE;1..100 rows; current unindexed selection from beginning. Exact canonical request replay BEFORE revision compare; changed reuse → IncidentConflict. Returns {schema_version:1,projected_count,checkpoint_revision,more_as_of_commit,coverage:'observed within this transaction; later inserts may be unindexed'}. Empty batch still increments revision; old replay never consumes late rows. Scope/request/receipt and checkpoint are atomic. Caller must establish configured operator boundary; this helper grants no runtime permission.

investigations.current(conn,selected,incident): initial revision0/None for no head, otherwise current immutable revision.
investigations.revise(conn,selected,incident,expected_revision,request_id,changes): owns transaction; exact request replay before revision comparison; requires catalog-indexed incident (legacy must first be explicitly projected). Immutable revision record {revision_id,revision,parent_revision_id,actor_kind:'local_dashboard_operator',request_id,created_at,document}; no named-user claim.
Document: schema_version1,incident_id,facts:[actual incident ID],original classification(category/classification_version/provenance),hypotheses,affected_components,controls,corrective_actions,corrections,status,rationale. Facts/classification cannot be overwritten; correction interpretations are separate.
Changes support:
- hypotheses [{cause:closed code,confidence:unassessed|low|medium|high,supporting:[actual scoped catalog IDs],contradicting:[IDs],operator_explanation?:bounded separately authored text}]. Nonunknown cause requires supporting evidence.
- affected_components [{reference:relative component path,verification:'unverified'}]. Verified-source resolver NEXT stage; no file read/claim inferred.
- controls [{kind:authority|dispatch|contract|budget|verification|review,state:held|failed|unknown,evidence:[IDs]}]. Known states require evidence; authority rejection defaults held.
- corrective_actions: closed action code OR {kind:closed action code,operator_explanation?:text}; suggestions only.
- corrections [{field:classification|hypotheses|affected_components|controls|status,interpretation:closed cause/control code,rationale:{code:closed reason,evidence:[IDs],operator_explanation?:text},operator_explanation?:text}]. Cumulative, prior revisions immutable.
- status open|investigating|resolved|dismissed. Resolution/dismissal requires newly supplied rationale+evidence, grants no approval/deployment/verification.
- rationale {code:closed reason,evidence:[IDs],operator_explanation?:text}.
Operator explanations <=2000 chars, reject recognized credential content via existing execution_logs.redact BEFORE persistence. This is separately attributed authored interpretation, NOT automatic imported source metadata. Credential recognition is bounded, not a general proof arbitrary authored text contains no unknown secret. Unknown automatic source fields/prose remain forbidden.
investigations.history(...before_revision=None,limit=25): immutable descending history;1..100; next_revision.

Grouping is bounded in the exact selected task/execution/epoch. It is NOT historical cross-execution discovery.
grouping.suggest(conn,selected,limit=100,cursor=None): explicit writer action; owns transaction; processes one catalog page <=100. Legacy-only members are skipped with count and remain visible in catalog. Closed boundary-rejected family relates AUTHORITY_REJECTED/DISPATCH_REJECTED/CONTRACT_REJECTED; other codes keep exact family. Safe key has actual scope+observable family, never raw text/hash. Returns schema_version1,scope,items,next_cursor,legacy_unindexed_in_window,coverage. Each group has group_id,algorithm_version1,safe_key,members,member_metadata(actual source/category/code/tool/role/policy/spec; null means unknown),created_at,revision,decision,meaning:'related symptoms; common cause not established'. Different versions/sources remain explicit, never cause proof.
Suggestion identity is task+algorithm+safe_key+sorted exact input IDs. Same-input replay returns same group and durable prior reject/split/supersede decision; new input is a new explicitly inspectable suggestion, never deletion of old decision/facts.
grouping.get(conn,selected,id): read existing group/current decision only.
grouping.decide(...expected_revision,request_id,decision): transactional exact request replay before CAS; kind accept|reject|split|supersede, evidence-backed rationale; split requires exact partition of every member once; supersede target must exist in same selected scope and cannot be self. Appends local_dashboard_operator decision, never merges/deletes incidents.
grouping.decisions(...before_revision=None,limit=25): bounded immutable decision history.

Next integration must add actual ledger/review immutable source preparation and adapters; real ContractError receipt capture/observation transaction+assignment-wait publication; actual policy hook/optional measured sidecar; real operator verdict producer; HTTP Host/Origin/content-type/body-cap/configured-scope enforcement; existing UI search/detail/revision/group journeys and actual desktop/narrow Chrome inspection. No such endpoint/producer/UI is claimed by this foundation.
Existing R1 store/query behavior is untouched. R6 consumer, R7 measured result, R5b standalone and full-mission phases remain pending. Old runtime pins are not upgraded.
