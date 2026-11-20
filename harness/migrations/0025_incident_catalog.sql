CREATE TABLE reliability_incident_catalog (
 incident_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version=1),
 source_kind TEXT NOT NULL CHECK(source_kind IN ('operation_dispatch','al_test_result','al_review_finding',
 'contract_rejection','budget_decision','operator_verdict')),
 task_id TEXT NOT NULL REFERENCES tasks(id), execution_id TEXT NOT NULL REFERENCES executions(id),
 epoch_id TEXT NOT NULL, category TEXT NOT NULL, classification_version INTEGER NOT NULL CHECK(classification_version=1),
 created_at TEXT NOT NULL, symptom_code TEXT NOT NULL,
 provenance TEXT NOT NULL CHECK(provenance IN ('runtime_observed','validated_ledger_result',
 'validated_review_finding','operator_reported','evaluation_measured')),
 operation_link TEXT GENERATED ALWAYS AS (CASE WHEN source_kind='operation_dispatch' THEN incident_id END) VIRTUAL
 REFERENCES incident_operation_sources(incident_id) DEFERRABLE INITIALLY DEFERRED,
 event_link TEXT GENERATED ALWAYS AS (CASE WHEN source_kind IN ('al_test_result','al_review_finding') THEN incident_id END) VIRTUAL
 REFERENCES incident_event_sources(incident_id) DEFERRABLE INITIALLY DEFERRED,
 contract_link TEXT GENERATED ALWAYS AS (CASE WHEN source_kind='contract_rejection' THEN incident_id END) VIRTUAL
 REFERENCES incident_contract_sources(incident_id) DEFERRABLE INITIALLY DEFERRED,
 policy_link TEXT GENERATED ALWAYS AS (CASE WHEN source_kind='budget_decision' THEN incident_id END) VIRTUAL
 REFERENCES incident_policy_sources(incident_id) DEFERRABLE INITIALLY DEFERRED,
 operator_link TEXT GENERATED ALWAYS AS (CASE WHEN source_kind='operator_verdict' THEN incident_id END) VIRTUAL
 REFERENCES incident_operator_sources(incident_id) DEFERRABLE INITIALLY DEFERRED,
 CHECK(
 (source_kind='operation_dispatch' AND provenance='runtime_observed' AND
 ((category='operation_denied' AND symptom_code IN ('AUTHORITY_REJECTED','DISPATCH_REJECTED')) OR
 (category='operation_failed' AND symptom_code IN ('DISPATCH_OS_ERROR','DISPATCH_PROCESS_ERROR','DISPATCH_INTERNAL_ERROR')))) OR
 (source_kind='al_test_result' AND provenance='validated_ledger_result' AND category='verification_failed' AND symptom_code='VERIFICATION_FAILED') OR
 (source_kind='al_review_finding' AND provenance='validated_review_finding' AND category IN
 ('review_finding','architecture_violation','specification_drift','incorrect_deliverable','hallucinated_dependency','hallucinated_api','incorrect_tool','missing_context') AND symptom_code='REVIEW_FINDING') OR
 (source_kind='contract_rejection' AND provenance='runtime_observed' AND category='invalid_handoff' AND symptom_code='CONTRACT_REJECTED') OR
 (source_kind='budget_decision' AND provenance='runtime_observed' AND category='budget_exhaustion' AND symptom_code='BUDGET_EXHAUSTED') OR
 (source_kind='operator_verdict' AND provenance='operator_reported' AND category='human_rejected_deliverable' AND symptom_code='OPERATOR_REJECTED')),
 UNIQUE(incident_id,source_kind), FOREIGN KEY(execution_id,epoch_id) REFERENCES execution_epochs(execution_id,epoch_id)
);
CREATE INDEX incident_catalog_scope ON reliability_incident_catalog(task_id,execution_id,epoch_id,created_at,incident_id);
CREATE TABLE incident_operation_sources (
 incident_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL DEFAULT 'operation_dispatch' CHECK(source_kind='operation_dispatch'),
 legacy_incident_id TEXT NOT NULL UNIQUE REFERENCES reliability_incidents(incident_id),
 FOREIGN KEY(incident_id,source_kind) REFERENCES reliability_incident_catalog(incident_id,source_kind)
);
CREATE TABLE incident_event_sources (
 incident_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL CHECK(source_kind IN ('al_test_result','al_review_finding')),
 execution_id TEXT NOT NULL, epoch_id TEXT NOT NULL, event_id TEXT NOT NULL,
 item_id TEXT NOT NULL, source_revision INTEGER NOT NULL CHECK(source_revision>=1),
 UNIQUE(source_kind,execution_id,epoch_id,item_id,source_revision),
 FOREIGN KEY(incident_id,source_kind) REFERENCES reliability_incident_catalog(incident_id,source_kind),
 FOREIGN KEY(execution_id,epoch_id,event_id) REFERENCES execution_events(execution_id,epoch_id,event_id)
);
CREATE TABLE contract_rejection_receipts (
 rejection_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 authority_generation INTEGER NOT NULL, principal_id TEXT NOT NULL, boundary TEXT NOT NULL,
 contract_family TEXT, error_code TEXT NOT NULL, field_pointer TEXT NOT NULL,
 observation_id TEXT REFERENCES reliability_observations(observation_id), created_at TEXT NOT NULL,
 FOREIGN KEY(execution_id,epoch_id) REFERENCES execution_epochs(execution_id,epoch_id)
);
CREATE TABLE incident_contract_sources (
 incident_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL DEFAULT 'contract_rejection' CHECK(source_kind='contract_rejection'),
 rejection_id TEXT NOT NULL UNIQUE REFERENCES contract_rejection_receipts(rejection_id),
 FOREIGN KEY(incident_id,source_kind) REFERENCES reliability_incident_catalog(incident_id,source_kind)
);
CREATE TABLE incident_policy_sources (
 incident_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL DEFAULT 'budget_decision' CHECK(source_kind='budget_decision'),
 policy_decision_id TEXT NOT NULL UNIQUE REFERENCES policy_decisions(id),
 FOREIGN KEY(incident_id,source_kind) REFERENCES reliability_incident_catalog(incident_id,source_kind)
);
CREATE TABLE incident_operator_sources (
 incident_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL DEFAULT 'operator_verdict' CHECK(source_kind='operator_verdict'),
 task_id TEXT NOT NULL REFERENCES tasks(id), operator_request_id TEXT NOT NULL,
 verdict_revision INTEGER NOT NULL CHECK(verdict_revision>=1), document TEXT NOT NULL,
 UNIQUE(task_id,operator_request_id),
 FOREIGN KEY(incident_id,source_kind) REFERENCES reliability_incident_catalog(incident_id,source_kind)
);
-- Evaluation relations are intentionally not fabricated before the R7 measured-result producer exists.
CREATE TABLE incident_projection_checkpoints (
 task_id TEXT NOT NULL REFERENCES tasks(id), execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=0), PRIMARY KEY(task_id,execution_id,epoch_id)
);
CREATE TABLE incident_projection_requests (
 task_id TEXT NOT NULL, execution_id TEXT NOT NULL, epoch_id TEXT NOT NULL, request_id TEXT NOT NULL,
 request TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(task_id,execution_id,epoch_id,request_id)
);
CREATE TABLE incident_investigation_revisions (
 revision_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES reliability_incident_catalog(incident_id),
 revision INTEGER NOT NULL CHECK(revision>=1), parent_revision_id TEXT REFERENCES incident_investigation_revisions(revision_id),
 schema_version INTEGER NOT NULL CHECK(schema_version=1), actor_kind TEXT NOT NULL CHECK(actor_kind='local_dashboard_operator'),
 request_id TEXT NOT NULL, request TEXT NOT NULL, created_at TEXT NOT NULL, document TEXT NOT NULL,
 UNIQUE(incident_id,revision), UNIQUE(incident_id,request_id)
);
CREATE TABLE incident_investigation_heads (
 incident_id TEXT PRIMARY KEY REFERENCES reliability_incident_catalog(incident_id), revision INTEGER NOT NULL,
 revision_id TEXT NOT NULL UNIQUE REFERENCES incident_investigation_revisions(revision_id)
);
CREATE TABLE incident_group_suggestions (
 group_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), algorithm_version INTEGER NOT NULL CHECK(algorithm_version=1),
 safe_key TEXT NOT NULL, input_set TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id,algorithm_version,safe_key,input_set)
);
CREATE TABLE incident_group_members (
 group_id TEXT NOT NULL REFERENCES incident_group_suggestions(group_id),
 incident_id TEXT NOT NULL REFERENCES reliability_incident_catalog(incident_id), PRIMARY KEY(group_id,incident_id)
);
CREATE TABLE incident_group_decisions (
 group_id TEXT NOT NULL REFERENCES incident_group_suggestions(group_id), revision INTEGER NOT NULL CHECK(revision>=1),
 request_id TEXT NOT NULL, request TEXT NOT NULL, actor_kind TEXT NOT NULL CHECK(actor_kind='local_dashboard_operator'),
 created_at TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(group_id,revision), UNIQUE(group_id,request_id)
);
CREATE TRIGGER investigation_no_update BEFORE UPDATE ON incident_investigation_revisions BEGIN SELECT RAISE(ABORT,'immutable investigation'); END;
CREATE TRIGGER investigation_no_delete BEFORE DELETE ON incident_investigation_revisions BEGIN SELECT RAISE(ABORT,'immutable investigation'); END;
CREATE TRIGGER group_decision_no_update BEFORE UPDATE ON incident_group_decisions BEGIN SELECT RAISE(ABORT,'immutable group decision'); END;
CREATE TRIGGER group_decision_no_delete BEFORE DELETE ON incident_group_decisions BEGIN SELECT RAISE(ABORT,'immutable group decision'); END;

CREATE TRIGGER operation_source_binding BEFORE INSERT ON incident_operation_sources
WHEN NEW.incident_id != NEW.legacy_incident_id OR NOT EXISTS (
 SELECT 1 FROM reliability_incident_catalog c JOIN reliability_incidents i ON i.incident_id=NEW.legacy_incident_id
 JOIN reliability_observations o USING(observation_id) WHERE c.incident_id=NEW.incident_id
 AND c.task_id=o.task_id AND c.execution_id=o.execution_id AND c.epoch_id=o.epoch_id
 AND c.category=i.category AND c.symptom_code=o.code AND c.created_at=i.created_at)
BEGIN SELECT RAISE(ABORT,'operation source binding'); END;
CREATE TRIGGER event_source_binding BEFORE INSERT ON incident_event_sources
WHEN NOT EXISTS (SELECT 1 FROM reliability_incident_catalog c WHERE c.incident_id=NEW.incident_id
 AND c.execution_id=NEW.execution_id AND c.epoch_id=NEW.epoch_id)
BEGIN SELECT RAISE(ABORT,'event source binding'); END;
CREATE TRIGGER policy_source_binding BEFORE INSERT ON incident_policy_sources
WHEN NOT EXISTS (SELECT 1 FROM reliability_incident_catalog c JOIN policy_decisions d ON d.id=NEW.policy_decision_id
 JOIN executions e ON e.id=d.execution_id WHERE c.incident_id=NEW.incident_id AND c.task_id=e.task_id
 AND c.execution_id=d.execution_id AND c.epoch_id=d.epoch_id AND d.verdict='deny' AND d.code='BUDGET_EXHAUSTED')
BEGIN SELECT RAISE(ABORT,'policy source binding'); END;
CREATE TRIGGER contract_source_binding BEFORE INSERT ON incident_contract_sources
WHEN NOT EXISTS (SELECT 1 FROM reliability_incident_catalog c JOIN contract_rejection_receipts r ON r.rejection_id=NEW.rejection_id
 JOIN executions e ON e.id=r.execution_id WHERE c.incident_id=NEW.incident_id AND c.task_id=e.task_id
 AND c.execution_id=r.execution_id AND c.epoch_id=r.epoch_id)
BEGIN SELECT RAISE(ABORT,'contract source binding'); END;
CREATE TRIGGER operator_source_binding BEFORE INSERT ON incident_operator_sources
WHEN NOT EXISTS (SELECT 1 FROM reliability_incident_catalog c WHERE c.incident_id=NEW.incident_id AND c.task_id=NEW.task_id)
BEGIN SELECT RAISE(ABORT,'operator source binding'); END;
CREATE TRIGGER reliability_incident_catalog_no_update BEFORE UPDATE ON reliability_incident_catalog BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER reliability_incident_catalog_no_delete BEFORE DELETE ON reliability_incident_catalog BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_operation_sources_no_update BEFORE UPDATE ON incident_operation_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_operation_sources_no_delete BEFORE DELETE ON incident_operation_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_event_sources_no_update BEFORE UPDATE ON incident_event_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_event_sources_no_delete BEFORE DELETE ON incident_event_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_policy_sources_no_update BEFORE UPDATE ON incident_policy_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_policy_sources_no_delete BEFORE DELETE ON incident_policy_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_contract_sources_no_update BEFORE UPDATE ON incident_contract_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_contract_sources_no_delete BEFORE DELETE ON incident_contract_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_operator_sources_no_update BEFORE UPDATE ON incident_operator_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_operator_sources_no_delete BEFORE DELETE ON incident_operator_sources BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER contract_rejection_receipts_no_update BEFORE UPDATE ON contract_rejection_receipts BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER contract_rejection_receipts_no_delete BEFORE DELETE ON contract_rejection_receipts BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_projection_requests_no_update BEFORE UPDATE ON incident_projection_requests BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_projection_requests_no_delete BEFORE DELETE ON incident_projection_requests BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_group_suggestions_no_update BEFORE UPDATE ON incident_group_suggestions BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_group_suggestions_no_delete BEFORE DELETE ON incident_group_suggestions BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_group_members_no_update BEFORE UPDATE ON incident_group_members BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER incident_group_members_no_delete BEFORE DELETE ON incident_group_members BEGIN SELECT RAISE(ABORT,'immutable incident source'); END;
CREATE TRIGGER catalog_task_binding BEFORE INSERT ON reliability_incident_catalog
WHEN NOT EXISTS (SELECT 1 FROM executions e WHERE e.id=NEW.execution_id AND e.task_id=NEW.task_id)
BEGIN SELECT RAISE(ABORT,'incident task binding'); END;
