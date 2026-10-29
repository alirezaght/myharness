ALTER TABLE executions ADD COLUMN epoch_id TEXT NOT NULL DEFAULT 'legacy-0';

ALTER TABLE executions ADD COLUMN authority_generation INTEGER NOT NULL DEFAULT 0;

CREATE TABLE execution_epochs (
 execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 authority_generation INTEGER NOT NULL, state TEXT NOT NULL,
 identity TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(execution_id,epoch_id), UNIQUE(execution_id,authority_generation)
);

INSERT INTO execution_epochs
 SELECT id,'legacy-0',0,'ACTIVE',json_object('al_session_id',al_session_id,'al_run_id',al_run_id,
 'handoff_path',handoff_path,'handoff_hash',handoff_hash),cursor,created_at FROM executions;

CREATE TABLE execution_runtime_transitions (
 request_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
 request TEXT NOT NULL, request_hash TEXT NOT NULL, state TEXT NOT NULL,
 preparation TEXT, activation TEXT, diagnostic TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

ALTER TABLE execution_events RENAME TO execution_events_legacy_migration;

CREATE TABLE execution_events (
 event_id TEXT NOT NULL, epoch_id TEXT NOT NULL DEFAULT 'legacy-0',
 execution_id TEXT NOT NULL REFERENCES executions(id),
 sequence INTEGER NOT NULL, kind TEXT NOT NULL, record TEXT NOT NULL,
 PRIMARY KEY(execution_id,epoch_id,event_id), UNIQUE(execution_id,epoch_id,sequence)
);

INSERT INTO execution_events (event_id,execution_id,sequence,kind,record) SELECT event_id,execution_id,sequence,kind,record FROM execution_events_legacy_migration;

DROP TABLE execution_events_legacy_migration;

ALTER TABLE execution_entities RENAME TO execution_entities_legacy_migration;

CREATE TABLE execution_entities (
 epoch_id TEXT NOT NULL DEFAULT 'legacy-0',
 execution_id TEXT NOT NULL REFERENCES executions(id), family TEXT NOT NULL,
 id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(execution_id,epoch_id,family,id)
);

INSERT INTO execution_entities (execution_id,family,id,data) SELECT execution_id,family,id,data FROM execution_entities_legacy_migration;

DROP TABLE execution_entities_legacy_migration;

ALTER TABLE execution_edges RENAME TO execution_edges_legacy_migration;

CREATE TABLE execution_edges (
 epoch_id TEXT NOT NULL DEFAULT 'legacy-0',
 execution_id TEXT NOT NULL REFERENCES executions(id), family TEXT NOT NULL,
 source TEXT NOT NULL, target TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(execution_id,epoch_id,family,source,target)
);

INSERT INTO execution_edges (execution_id,family,source,target,data) SELECT execution_id,family,source,target,data FROM execution_edges_legacy_migration;

DROP TABLE execution_edges_legacy_migration;

ALTER TABLE execution_decisions RENAME TO execution_decisions_legacy_migration;

CREATE TABLE execution_decisions (
 epoch_id TEXT NOT NULL DEFAULT 'legacy-0',
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
 blocker_id TEXT NOT NULL, blocker_revision INTEGER NOT NULL, request_id TEXT NOT NULL,
 request TEXT NOT NULL, state TEXT NOT NULL, registry_id TEXT, registry_path TEXT,
 registry_bytes TEXT, registry_hash TEXT, delivery TEXT, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(execution_id,epoch_id,request_id)
);

INSERT INTO execution_decisions (id,execution_id,blocker_id,blocker_revision,request_id,request,state,registry_id,registry_path,registry_bytes,registry_hash,delivery,error,created_at,updated_at) SELECT id,execution_id,blocker_id,blocker_revision,request_id,request,state,registry_id,registry_path,registry_bytes,registry_hash,delivery,error,created_at,updated_at FROM execution_decisions_legacy_migration;

DROP TABLE execution_decisions_legacy_migration;

ALTER TABLE execution_attempts ADD COLUMN epoch_id TEXT NOT NULL DEFAULT 'legacy-0';

ALTER TABLE execution_logs ADD COLUMN epoch_id TEXT NOT NULL DEFAULT 'legacy-0';

ALTER TABLE execution_initializations RENAME TO execution_initializations_legacy_migration;
CREATE TABLE execution_initializations (
 execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL DEFAULT 'legacy-0',
 state TEXT NOT NULL, identity TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(execution_id,epoch_id)
);
INSERT INTO execution_initializations(execution_id,state,identity,updated_at)
 SELECT execution_id,state,identity,updated_at FROM execution_initializations_legacy_migration;
DROP TABLE execution_initializations_legacy_migration;

ALTER TABLE deliveries ADD COLUMN epoch_id TEXT NOT NULL DEFAULT 'legacy-0';
ALTER TABLE delivery_operations ADD COLUMN epoch_id TEXT NOT NULL DEFAULT 'legacy-0';
ALTER TABLE delivery_corrections ADD COLUMN epoch_id TEXT NOT NULL DEFAULT 'legacy-0';
