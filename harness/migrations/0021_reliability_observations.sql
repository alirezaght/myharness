CREATE TABLE reliability_observations (
 observation_id TEXT PRIMARY KEY,
 schema_version INTEGER NOT NULL CHECK(schema_version=1),
 source_kind TEXT NOT NULL CHECK(source_kind='operation-dispatch'),
 source_id TEXT NOT NULL UNIQUE REFERENCES operation_audit(id),
 execution_id TEXT NOT NULL REFERENCES executions(id), task_id TEXT NOT NULL REFERENCES tasks(id),
 epoch_id TEXT NOT NULL, authority_generation INTEGER NOT NULL CHECK(authority_generation>=0),
 principal_id TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('orchestrator','al-architect',
 'al-architect-fallback','al-designer','al-designer-fallback','al-engineer','al-builder','al-scout')),
 tool TEXT NOT NULL CHECK(tool IN ('unknown','read_file','write_artifact','submit_output',
 'apply_patch','run_command','poll_process','cancel_process','al_command','diagnostic',
 'delivery_operation','environment_operation','diagram_operation','knowledge_query','knowledge_explain')),
 policy_digest TEXT, spec_version INTEGER NOT NULL, approval_id INTEGER NOT NULL REFERENCES approvals(id),
 started_at TEXT NOT NULL, finished_at TEXT,
 state TEXT NOT NULL CHECK(state IN ('pending','succeeded','denied','failed')),
 code TEXT, provenance TEXT NOT NULL CHECK(provenance='runtime_observed'),
 UNIQUE(source_kind,source_id),
 CHECK((state='pending' AND code IS NULL AND finished_at IS NULL) OR
 (finished_at IS NOT NULL AND code IS NOT NULL AND ((state='succeeded' AND code='RETURNED') OR
 (state='denied' AND code IN ('AUTHORITY_REJECTED','DISPATCH_REJECTED')) OR
 (state='failed' AND code IN ('DISPATCH_OS_ERROR','DISPATCH_PROCESS_ERROR','DISPATCH_INTERNAL_ERROR')))))
);
CREATE INDEX idx_reliability_scope ON reliability_observations(execution_id,epoch_id,started_at,observation_id);
CREATE TABLE reliability_incidents (
 incident_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL UNIQUE REFERENCES reliability_observations(observation_id),
 schema_version INTEGER NOT NULL CHECK(schema_version=1),
 category TEXT NOT NULL CHECK(category IN ('operation_denied','operation_failed')),
 status TEXT NOT NULL CHECK(status='open'), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 classification_version INTEGER NOT NULL CHECK(classification_version=1)
);
CREATE INDEX idx_reliability_incidents_page ON reliability_incidents(created_at,incident_id);
