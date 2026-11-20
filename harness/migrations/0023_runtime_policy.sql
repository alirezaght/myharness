CREATE TABLE policy_versions (
 id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 scope_kind TEXT NOT NULL, scope_ref TEXT NOT NULL, document TEXT NOT NULL,
 digest TEXT NOT NULL, decision_ref TEXT NOT NULL REFERENCES execution_decisions(id),
 UNIQUE(policy_id,version)
);
CREATE TABLE policy_bindings (
 policy_id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES policy_versions(id), revision INTEGER NOT NULL
);
CREATE TABLE policy_origins (
 execution_id TEXT PRIMARY KEY REFERENCES executions(id), wall REAL NOT NULL,
 monotonic REAL NOT NULL, clock_id TEXT
);
CREATE TABLE policy_workflows (
 id TEXT PRIMARY KEY, decision_ref TEXT NOT NULL REFERENCES execution_decisions(id),
 wall REAL NOT NULL, monotonic REAL NOT NULL, clock_id TEXT
);
CREATE TABLE policy_memberships (
 execution_id TEXT PRIMARY KEY REFERENCES executions(id), root_id TEXT NOT NULL REFERENCES executions(id),
 workflow_id TEXT REFERENCES policy_workflows(id)
);
CREATE TABLE policy_pools (
 id TEXT PRIMARY KEY, dimension TEXT NOT NULL, domain TEXT NOT NULL, owner_ref TEXT NOT NULL,
 maximum INTEGER NOT NULL CHECK(maximum>=0 AND maximum<=9007199254740991),
 consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed>=0), reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0),
 revision INTEGER NOT NULL DEFAULT 1, version_id TEXT NOT NULL REFERENCES policy_versions(id),
 origin_wall REAL, deadline_wall REAL, origin_monotonic REAL, clock_id TEXT,
 last_wall REAL, state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','clock_uncertain'))
);
CREATE TABLE policy_capabilities (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 generation INTEGER NOT NULL, document TEXT NOT NULL, decision_ref TEXT NOT NULL REFERENCES execution_decisions(id),
 uses INTEGER NOT NULL DEFAULT 0 CHECK(uses>=0), units INTEGER NOT NULL DEFAULT 0 CHECK(units>=0),
 issued_wall REAL NOT NULL, issued_monotonic REAL NOT NULL, clock_id TEXT, last_wall REAL NOT NULL,
 revocation_generation INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','revoked'))
);
CREATE TABLE policy_capability_actions (
 action_id TEXT PRIMARY KEY, capability_id TEXT NOT NULL REFERENCES policy_capabilities(id),
 execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 decision_ref TEXT NOT NULL REFERENCES execution_decisions(id),
 action TEXT NOT NULL CHECK(action IN ('issued','revoked')),
 revocation_generation INTEGER NOT NULL CHECK(revocation_generation>=0), created_at TEXT NOT NULL,
 UNIQUE(capability_id,action,revocation_generation)
);
CREATE TABLE policy_attempts (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 legacy INTEGER NOT NULL DEFAULT 0 CHECK(legacy IN (0,1)),
 generation INTEGER NOT NULL, supervisor_id TEXT NOT NULL, principal_id TEXT NOT NULL, role TEXT NOT NULL, task_id TEXT, revision TEXT,
 observation_id TEXT REFERENCES reliability_observations(observation_id), operation TEXT NOT NULL,
 binding TEXT NOT NULL, policy_digest TEXT NOT NULL, versions TEXT NOT NULL, retry_reference TEXT, resources TEXT NOT NULL, boundary TEXT NOT NULL, remote_liability TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','started','settled','unresolved','released_proven_not_started')),
 admitted_at TEXT NOT NULL, started_at TEXT, settled_at TEXT, outcome TEXT,
 deadline_wall REAL, permit_id TEXT UNIQUE, launcher_permit TEXT UNIQUE, retry_claim TEXT UNIQUE,
 UNIQUE(execution_id,epoch_id,observation_id)
);
CREATE TABLE policy_resource_claims (
 attempt_id TEXT NOT NULL REFERENCES policy_attempts(id), position INTEGER NOT NULL,
 claim TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('reserved','active','unresolved','closed')),
 PRIMARY KEY(attempt_id,position)
);
CREATE TABLE policy_resource_domains (
 repository TEXT NOT NULL, resource TEXT NOT NULL,
 decision_ref TEXT NOT NULL REFERENCES execution_decisions(id), PRIMARY KEY(repository,resource)
);
CREATE TABLE policy_legacy_admissions (
 attempt_id TEXT PRIMARY KEY REFERENCES policy_attempts(id), resources TEXT NOT NULL
);
CREATE TABLE policy_retry_claims (
 execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 al_run_id TEXT NOT NULL, al_session_id TEXT NOT NULL, target_role TEXT NOT NULL,
 checkpoint_ref TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE REFERENCES policy_attempts(id),
 grant_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 PRIMARY KEY(execution_id,al_run_id,target_role,checkpoint_ref)
);
CREATE TABLE policy_reservations (
 attempt_id TEXT NOT NULL REFERENCES policy_attempts(id), pool_id TEXT NOT NULL REFERENCES policy_pools(id),
 units INTEGER NOT NULL CHECK(units>=0), charged INTEGER NOT NULL DEFAULT 0 CHECK(charged IN (0,1)), observed_units INTEGER CHECK(observed_units>=0),
 PRIMARY KEY(attempt_id,pool_id)
);
CREATE TABLE policy_capability_uses (
 attempt_id TEXT NOT NULL REFERENCES policy_attempts(id), capability_id TEXT NOT NULL REFERENCES policy_capabilities(id),
 units INTEGER NOT NULL CHECK(units>=0), PRIMARY KEY(attempt_id,capability_id)
);
CREATE TABLE policy_decisions (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 attempt_id TEXT REFERENCES policy_attempts(id), verdict TEXT NOT NULL CHECK(verdict IN ('allow','deny')),
 code TEXT NOT NULL, policy_digest TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE policy_processes (
 id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES policy_attempts(id),
 span_id TEXT REFERENCES reliability_process_spans(span_id), observed_attempt_id TEXT,
 handle TEXT NOT NULL, instance_ref TEXT NOT NULL UNIQUE, pid INTEGER, parent_reaped INTEGER NOT NULL DEFAULT 0,
 owned_group_absent INTEGER NOT NULL DEFAULT 0, boundary_closed INTEGER NOT NULL DEFAULT 0, launch_state TEXT NOT NULL CHECK(launch_state IN ('pending','launched','unknown')),
 closure TEXT NOT NULL DEFAULT 'unconfirmed' CHECK(closure IN ('unconfirmed','owned_group_closed')),
 UNIQUE(attempt_id,handle)
);
CREATE INDEX policy_attempt_scope ON policy_attempts(execution_id,epoch_id,state);
CREATE INDEX policy_decision_scope ON policy_decisions(execution_id,epoch_id,created_at,id);
ALTER TABLE reliability_timeline_links RENAME TO reliability_timeline_links_r2;
CREATE TABLE reliability_timeline_links (
 link_id TEXT PRIMARY KEY NOT NULL, trace_id TEXT NOT NULL REFERENCES reliability_traces(trace_id),
 execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 source_kind TEXT NOT NULL CHECK(source_kind IN ('process','semantic','retrieval','policy')),
 source_id TEXT NOT NULL, event_kind TEXT NOT NULL CHECK(event_kind IN (
 'process.launch_pending','process.started','process.launch_failed','process.cancel_requested',
 'process.timeout_observed','process.exited','process.cleanup_unconfirmed',
 'semantic.agent','semantic.task','semantic.check','semantic.decision','semantic.output','semantic.run',
 'retrieval.bundle','policy.allowed','policy.denied','policy.started','policy.settled','policy.unresolved','policy.capability_issued','policy.capability_revoked')),
 provenance TEXT NOT NULL CHECK(provenance IN ('runtime_observed','validated_semantic_report','historical_reference','policy_authority')),
 created_at TEXT NOT NULL, principal_id TEXT,
 parent_observation_id TEXT REFERENCES reliability_observations(observation_id),
 parent_span_id TEXT REFERENCES reliability_process_spans(span_id),
 reference_id TEXT, bundle_hash TEXT, snapshot_id TEXT, receipt_id TEXT,
 UNIQUE(execution_id,epoch_id,source_kind,source_id,event_kind)
);
INSERT INTO reliability_timeline_links SELECT * FROM reliability_timeline_links_r2;
DROP TABLE reliability_timeline_links_r2;
CREATE INDEX idx_reliability_timeline ON reliability_timeline_links(execution_id,epoch_id,created_at,link_id);

CREATE TABLE policy_control_helpers (
 id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES policy_attempts(id),
 method TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','started','closed','unknown')),
 created_at TEXT NOT NULL, pid INTEGER, exit_code INTEGER, observed_bytes INTEGER
);
CREATE TABLE policy_control_receipts (
 id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE REFERENCES policy_attempts(id),
 qualification TEXT NOT NULL, helper_count INTEGER NOT NULL, exit_code INTEGER NOT NULL,
 created_at TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('prepared','closed'))
);
