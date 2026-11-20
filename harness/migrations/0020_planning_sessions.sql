CREATE TABLE planning_session_epochs (
 task_id TEXT NOT NULL REFERENCES tasks(id), project TEXT NOT NULL, epoch INTEGER NOT NULL CHECK(epoch>0),
 attachment_id TEXT NOT NULL UNIQUE, provider TEXT NOT NULL CHECK(provider IN ('codex','claude')),
 root_session_id TEXT NOT NULL, current_thread_id TEXT NOT NULL, protocol_version INTEGER NOT NULL CHECK(protocol_version=1),
 capability_verifier TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(task_id,project,epoch), UNIQUE(task_id,project,epoch,attachment_id)
);
CREATE TABLE planning_session_owners (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), project TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('attached','standalone','manual')), epoch INTEGER NOT NULL CHECK(epoch>=0),
 attachment_id TEXT, input_revision INTEGER NOT NULL DEFAULT 0 CHECK(input_revision>=0),
 status_revision INTEGER NOT NULL DEFAULT 0, next_sequence INTEGER NOT NULL DEFAULT 1,
 FOREIGN KEY(task_id,project,epoch,attachment_id) REFERENCES planning_session_epochs(task_id,project,epoch,attachment_id)
);
CREATE TABLE planning_session_contacts (
 task_id TEXT NOT NULL, project TEXT NOT NULL, epoch INTEGER NOT NULL, state TEXT NOT NULL,
 last_wall REAL NOT NULL, last_monotonic REAL NOT NULL, deadline_wall REAL NOT NULL, deadline_monotonic REAL NOT NULL,
 wait_id TEXT, wait_deadline_monotonic REAL, boot_id TEXT NOT NULL,
 PRIMARY KEY(task_id,project,epoch), FOREIGN KEY(task_id,project,epoch) REFERENCES planning_session_epochs(task_id,project,epoch)
);
CREATE TABLE planning_session_events (
 task_id TEXT NOT NULL REFERENCES tasks(id), sequence INTEGER NOT NULL, id TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('browser','tui_user','attached_agent','system','legacy_cli')),
 epoch INTEGER, semantic_base_json TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
 canonical_reference TEXT, deliverable INTEGER NOT NULL CHECK(deliverable IN (0,1)), material INTEGER NOT NULL CHECK(material IN (0,1)),
 PRIMARY KEY(task_id,sequence), UNIQUE(task_id,canonical_reference), UNIQUE(task_id,id)
);
CREATE TABLE planning_session_deliveries (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, project TEXT NOT NULL, epoch INTEGER NOT NULL,
 request_id TEXT NOT NULL, base_json TEXT NOT NULL, event_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(task_id,epoch,request_id), FOREIGN KEY(task_id,project,epoch) REFERENCES planning_session_epochs(task_id,project,epoch)
);
CREATE TABLE planning_session_acknowledgements (
 delivery_id TEXT NOT NULL REFERENCES planning_session_deliveries(id), event_id TEXT NOT NULL REFERENCES planning_session_events(id),
 received_at TEXT, applied_result_id TEXT, applied_at TEXT, PRIMARY KEY(delivery_id,event_id)
);
CREATE TABLE planning_session_event_results (
 task_id TEXT NOT NULL, event_id TEXT NOT NULL, result_id TEXT NOT NULL, result_kind TEXT NOT NULL,
 epoch INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(task_id,event_id),
 FOREIGN KEY(task_id,event_id) REFERENCES planning_session_events(task_id,id)
);
CREATE TABLE planning_session_receipts (
 scope TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(scope,request_id)
);
CREATE TABLE planning_session_publications (
 task_id TEXT NOT NULL, project TEXT NOT NULL, epoch INTEGER NOT NULL, attachment_id TEXT NOT NULL,
 job_id TEXT NOT NULL, attempt INTEGER NOT NULL, request_id TEXT NOT NULL,
 UNIQUE(job_id,attempt), UNIQUE(task_id,epoch,request_id),
 FOREIGN KEY(task_id,project,epoch,attachment_id) REFERENCES planning_session_epochs(task_id,project,epoch,attachment_id),
 FOREIGN KEY(task_id,job_id) REFERENCES authoring_jobs(task_id,id),
 FOREIGN KEY(job_id,attempt) REFERENCES authoring_attempts(job_id,attempt)
);
CREATE TRIGGER attached_publication_insert BEFORE INSERT ON authoring_jobs
WHEN NEW.kind='attached_publication' AND (NEW.host!='attached' OR NEW.state!='succeeded' OR NEW.settlement!='settled')
BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER attached_publication_update BEFORE UPDATE ON authoring_jobs
WHEN (OLD.kind='attached_publication' OR NEW.kind='attached_publication') AND
 (NEW.kind!=OLD.kind OR NEW.host!='attached' OR NEW.state!='succeeded' OR NEW.settlement!='settled' OR NEW.attempt!=1)
BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER attached_attempt_insert BEFORE INSERT ON authoring_attempts
WHEN EXISTS(SELECT 1 FROM authoring_jobs WHERE id=NEW.job_id AND kind='attached_publication') AND
 (NEW.attempt!=1 OR NEW.state!='succeeded' OR NEW.settlement!='settled' OR NEW.owner IS NOT NULL OR NEW.claim!=0)
BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER attached_attempt_update BEFORE UPDATE ON authoring_attempts
WHEN EXISTS(SELECT 1 FROM authoring_jobs WHERE id=NEW.job_id AND kind='attached_publication') AND
 (NEW.state!='succeeded' OR NEW.settlement!='settled' OR NEW.attempt!=1 OR NEW.owner IS NOT NULL OR NEW.claim!=0)
BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER session_tasks_insert BEFORE INSERT ON tasks
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.id AND mode='attached') AND harness_session_write_allowed(NEW.id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_tasks_update BEFORE UPDATE ON tasks
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.id AND mode='attached') AND harness_session_write_allowed(OLD.id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.id AND mode='attached') AND harness_session_write_allowed(NEW.id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_tasks_delete BEFORE DELETE ON tasks
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.id AND mode='attached') AND harness_session_write_allowed(OLD.id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_phase_transitions_insert BEFORE INSERT ON phase_transitions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_phase_transitions_update BEFORE UPDATE ON phase_transitions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_phase_transitions_delete BEFORE DELETE ON phase_transitions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_questions_insert BEFORE INSERT ON questions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_questions_update BEFORE UPDATE ON questions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_questions_delete BEFORE DELETE ON questions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_proposals_insert BEFORE INSERT ON proposals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_proposals_update BEFORE UPDATE ON proposals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_proposals_delete BEFORE DELETE ON proposals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_events_insert BEFORE INSERT ON events
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_events_update BEFORE UPDATE ON events
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_events_delete BEFORE DELETE ON events
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_checkpoints_insert BEFORE INSERT ON checkpoints
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_checkpoints_update BEFORE UPDATE ON checkpoints
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_checkpoints_delete BEFORE DELETE ON checkpoints
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_design_entries_insert BEFORE INSERT ON design_entries
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_design_entries_update BEFORE UPDATE ON design_entries
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_design_entries_delete BEFORE DELETE ON design_entries
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_decision_changes_insert BEFORE INSERT ON decision_changes
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_decision_changes_update BEFORE UPDATE ON decision_changes
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_decision_changes_delete BEFORE DELETE ON decision_changes
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_test_scenarios_insert BEFORE INSERT ON test_scenarios
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_test_scenarios_update BEFORE UPDATE ON test_scenarios
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_test_scenarios_delete BEFORE DELETE ON test_scenarios
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_test_plan_approvals_insert BEFORE INSERT ON test_plan_approvals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_test_plan_approvals_update BEFORE UPDATE ON test_plan_approvals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_test_plan_approvals_delete BEFORE DELETE ON test_plan_approvals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_spec_versions_insert BEFORE INSERT ON spec_versions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_spec_versions_update BEFORE UPDATE ON spec_versions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_spec_versions_delete BEFORE DELETE ON spec_versions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_diagram_versions_insert BEFORE INSERT ON diagram_versions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_diagram_versions_update BEFORE UPDATE ON diagram_versions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_diagram_versions_delete BEFORE DELETE ON diagram_versions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_approvals_insert BEFORE INSERT ON approvals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_approvals_update BEFORE UPDATE ON approvals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_approvals_delete BEFORE DELETE ON approvals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_workspace_proposals_insert BEFORE INSERT ON workspace_proposals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_workspace_proposals_update BEFORE UPDATE ON workspace_proposals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_workspace_proposals_delete BEFORE DELETE ON workspace_proposals
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_tasks_insert BEFORE INSERT ON authoring_tasks
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_tasks_update BEFORE UPDATE ON authoring_tasks
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_tasks_delete BEFORE DELETE ON authoring_tasks
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_graphs_insert BEFORE INSERT ON authoring_graphs
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_graphs_update BEFORE UPDATE ON authoring_graphs
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_graphs_delete BEFORE DELETE ON authoring_graphs
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_revisions_insert BEFORE INSERT ON authoring_revisions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_revisions_update BEFORE UPDATE ON authoring_revisions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_revisions_delete BEFORE DELETE ON authoring_revisions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_contributions_insert BEFORE INSERT ON authoring_contributions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_contributions_update BEFORE UPDATE ON authoring_contributions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_contributions_delete BEFORE DELETE ON authoring_contributions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_jobs_insert BEFORE INSERT ON authoring_jobs
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_jobs_update BEFORE UPDATE ON authoring_jobs
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_jobs_delete BEFORE DELETE ON authoring_jobs
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_projections_insert BEFORE INSERT ON authoring_projections
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_projections_update BEFORE UPDATE ON authoring_projections
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_projections_delete BEFORE DELETE ON authoring_projections
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_bindings_insert BEFORE INSERT ON investigation_bindings
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_bindings_update BEFORE UPDATE ON investigation_bindings
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_bindings_delete BEFORE DELETE ON investigation_bindings
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_bundles_insert BEFORE INSERT ON investigation_bundles
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_bundles_update BEFORE UPDATE ON investigation_bundles
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_bundles_delete BEFORE DELETE ON investigation_bundles
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_sources_insert BEFORE INSERT ON investigation_sources
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_sources_update BEFORE UPDATE ON investigation_sources
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_sources_delete BEFORE DELETE ON investigation_sources
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_acceptances_insert BEFORE INSERT ON investigation_acceptances
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_acceptances_update BEFORE UPDATE ON investigation_acceptances
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_acceptances_delete BEFORE DELETE ON investigation_acceptances
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_decisions_insert BEFORE INSERT ON investigation_decisions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_decisions_update BEFORE UPDATE ON investigation_decisions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_decisions_delete BEFORE DELETE ON investigation_decisions
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_attempts_insert BEFORE INSERT ON authoring_attempts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=(SELECT task_id FROM authoring_jobs WHERE id=NEW.job_id) AND mode='attached') AND harness_session_write_allowed((SELECT task_id FROM authoring_jobs WHERE id=NEW.job_id))!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_attempts_update BEFORE UPDATE ON authoring_attempts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=(SELECT task_id FROM authoring_jobs WHERE id=OLD.job_id) AND mode='attached') AND harness_session_write_allowed((SELECT task_id FROM authoring_jobs WHERE id=OLD.job_id))!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=(SELECT task_id FROM authoring_jobs WHERE id=NEW.job_id) AND mode='attached') AND harness_session_write_allowed((SELECT task_id FROM authoring_jobs WHERE id=NEW.job_id))!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_attempts_delete BEFORE DELETE ON authoring_attempts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=(SELECT task_id FROM authoring_jobs WHERE id=OLD.job_id) AND mode='attached') AND harness_session_write_allowed((SELECT task_id FROM authoring_jobs WHERE id=OLD.job_id))!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_authoring_receipts_insert BEFORE INSERT ON authoring_receipts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_authoring_receipts_update BEFORE UPDATE ON authoring_receipts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_authoring_receipts_delete BEFORE DELETE ON authoring_receipts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_investigation_progress_insert BEFORE INSERT ON investigation_progress
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=(SELECT task_id FROM authoring_jobs WHERE id=NEW.job_id) AND mode='attached') AND harness_session_write_allowed((SELECT task_id FROM authoring_jobs WHERE id=NEW.job_id))!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_progress_update BEFORE UPDATE ON investigation_progress
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=(SELECT task_id FROM authoring_jobs WHERE id=OLD.job_id) AND mode='attached') AND harness_session_write_allowed((SELECT task_id FROM authoring_jobs WHERE id=OLD.job_id))!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=(SELECT task_id FROM authoring_jobs WHERE id=NEW.job_id) AND mode='attached') AND harness_session_write_allowed((SELECT task_id FROM authoring_jobs WHERE id=NEW.job_id))!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_investigation_progress_delete BEFORE DELETE ON investigation_progress
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=(SELECT task_id FROM authoring_jobs WHERE id=OLD.job_id) AND mode='attached') AND harness_session_write_allowed((SELECT task_id FROM authoring_jobs WHERE id=OLD.job_id))!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_planning_session_owners_insert BEFORE INSERT ON planning_session_owners
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_owners_update BEFORE UPDATE ON planning_session_owners
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_owners_delete BEFORE DELETE ON planning_session_owners
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_epochs_insert BEFORE INSERT ON planning_session_epochs
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_epochs_update BEFORE UPDATE ON planning_session_epochs
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_epochs_delete BEFORE DELETE ON planning_session_epochs
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_contacts_insert BEFORE INSERT ON planning_session_contacts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_contacts_update BEFORE UPDATE ON planning_session_contacts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_contacts_delete BEFORE DELETE ON planning_session_contacts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_events_insert BEFORE INSERT ON planning_session_events
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_events_update BEFORE UPDATE ON planning_session_events
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_events_delete BEFORE DELETE ON planning_session_events
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_deliveries_insert BEFORE INSERT ON planning_session_deliveries
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_deliveries_update BEFORE UPDATE ON planning_session_deliveries
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_deliveries_delete BEFORE DELETE ON planning_session_deliveries
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_acknowledgements_insert BEFORE INSERT ON planning_session_acknowledgements
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_acknowledgements_update BEFORE UPDATE ON planning_session_acknowledgements
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_acknowledgements_delete BEFORE DELETE ON planning_session_acknowledgements
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_event_results_insert BEFORE INSERT ON planning_session_event_results
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_event_results_update BEFORE UPDATE ON planning_session_event_results
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_event_results_delete BEFORE DELETE ON planning_session_event_results
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_receipts_insert BEFORE INSERT ON planning_session_receipts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_receipts_update BEFORE UPDATE ON planning_session_receipts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_receipts_delete BEFORE DELETE ON planning_session_receipts
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_publications_insert BEFORE INSERT ON planning_session_publications
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_publications_update BEFORE UPDATE ON planning_session_publications
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER session_planning_session_publications_delete BEFORE DELETE ON planning_session_publications
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
END;
CREATE TRIGGER immutable_planning_session_epochs_update BEFORE UPDATE ON planning_session_epochs BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_epochs_delete BEFORE DELETE ON planning_session_epochs BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_events_update BEFORE UPDATE ON planning_session_events BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_events_delete BEFORE DELETE ON planning_session_events BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_deliveries_update BEFORE UPDATE ON planning_session_deliveries BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_deliveries_delete BEFORE DELETE ON planning_session_deliveries BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_event_results_update BEFORE UPDATE ON planning_session_event_results BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_event_results_delete BEFORE DELETE ON planning_session_event_results BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_receipts_update BEFORE UPDATE ON planning_session_receipts BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_receipts_delete BEFORE DELETE ON planning_session_receipts BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_publications_update BEFORE UPDATE ON planning_session_publications BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER immutable_planning_session_publications_delete BEFORE DELETE ON planning_session_publications BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER session_comments_insert BEFORE INSERT ON comments BEGIN SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_comments_update BEFORE UPDATE ON comments BEGIN SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_comments_delete BEFORE DELETE ON comments BEGIN SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;

CREATE TABLE planning_session_handling (
 task_id TEXT NOT NULL, input_id TEXT NOT NULL, handling_kind TEXT NOT NULL,
 result_reference TEXT NOT NULL, material_revision INTEGER NOT NULL, material_digest TEXT NOT NULL,
 receipt_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 PRIMARY KEY(task_id,input_id), FOREIGN KEY(task_id,input_id) REFERENCES planning_session_events(task_id,id)
);
CREATE TRIGGER session_handling_insert BEFORE INSERT ON planning_session_handling
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER session_handling_immutable_update BEFORE UPDATE ON planning_session_handling BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TRIGGER session_handling_immutable_delete BEFORE DELETE ON planning_session_handling BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
CREATE TABLE investigation_artifact_policies (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), project TEXT NOT NULL, policy_json TEXT NOT NULL
);
CREATE TRIGGER artifact_policy_insert BEFORE INSERT ON investigation_artifact_policies
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=NEW.task_id AND mode='attached') AND harness_session_write_allowed(NEW.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER artifact_policy_update BEFORE UPDATE ON investigation_artifact_policies
BEGIN
 SELECT CASE WHEN harness_session_protocol_version()!=1 THEN RAISE(ABORT,'SESSION_UPGRADE_REQUIRED') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM planning_session_owners WHERE task_id=OLD.task_id AND mode='attached') AND harness_session_write_allowed(OLD.task_id)!=1 THEN RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE') END;
END;
CREATE TRIGGER artifact_policy_delete BEFORE DELETE ON investigation_artifact_policies
BEGIN SELECT RAISE(ABORT,'SESSION_OPERATION_INCOMPATIBLE'); END;
