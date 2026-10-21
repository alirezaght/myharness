CREATE TABLE executions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 approval_id INTEGER NOT NULL UNIQUE REFERENCES approvals(id), host TEXT NOT NULL CHECK(host IN ('claude','codex')),
 binding TEXT NOT NULL, handoff_path TEXT NOT NULL, handoff_hash TEXT NOT NULL,
 status TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
 al_session_id TEXT NOT NULL, al_run_id TEXT, native_id TEXT, worker_token TEXT,
 source_contact REAL, heartbeat REAL, error TEXT, result TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_executions_task ON executions(task_id, created_at);
CREATE TABLE execution_attempts (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id), token TEXT NOT NULL UNIQUE,
 mode TEXT NOT NULL, status TEXT NOT NULL, worker_pid INTEGER, child_pid INTEGER, native_id TEXT,
 package TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE execution_events (
 event_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
 sequence INTEGER NOT NULL, kind TEXT NOT NULL, record TEXT NOT NULL,
 UNIQUE(execution_id, sequence)
);
CREATE TABLE execution_entities (
 execution_id TEXT NOT NULL REFERENCES executions(id), family TEXT NOT NULL,
 id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(execution_id, family, id)
);
CREATE TABLE execution_edges (
 execution_id TEXT NOT NULL REFERENCES executions(id), family TEXT NOT NULL,
 source TEXT NOT NULL, target TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(execution_id, family, source, target)
);
CREATE TABLE execution_decisions (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
 blocker_id TEXT NOT NULL, blocker_revision INTEGER NOT NULL, request_id TEXT NOT NULL,
 request TEXT NOT NULL, state TEXT NOT NULL, registry_id TEXT, registry_path TEXT,
 registry_bytes TEXT, registry_hash TEXT, delivery TEXT, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(execution_id, request_id)
);
CREATE TABLE execution_logs (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
 channel TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
);
