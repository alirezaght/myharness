CREATE TABLE deliveries (
 execution_id TEXT PRIMARY KEY REFERENCES executions(id), binding TEXT NOT NULL,
 config TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'Configured', candidate_sha TEXT, mr TEXT,
 snapshot TEXT NOT NULL DEFAULT '{}', retries INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
CREATE TABLE delivery_operations (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
 generation INTEGER NOT NULL, kind TEXT NOT NULL, candidate_sha TEXT NOT NULL,
 request TEXT NOT NULL, state TEXT NOT NULL, result TEXT, updated_at TEXT NOT NULL,
 UNIQUE(execution_id,generation,kind,candidate_sha)
);
CREATE TABLE delivery_corrections (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
 generation INTEGER NOT NULL, candidate_sha TEXT NOT NULL, pipeline_id INTEGER NOT NULL,
 request TEXT NOT NULL, state TEXT NOT NULL, acknowledgement TEXT,
 UNIQUE(execution_id,candidate_sha,pipeline_id)
);
CREATE TABLE operation_audit (
 id TEXT PRIMARY KEY, execution_id TEXT REFERENCES executions(id),
 request TEXT NOT NULL, decision TEXT NOT NULL, outcome TEXT, created_at TEXT NOT NULL
);
CREATE TABLE operation_approvals (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
 request_digest TEXT NOT NULL, binding TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE execution_initializations (
 execution_id TEXT PRIMARY KEY REFERENCES executions(id), state TEXT NOT NULL,
 identity TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE execution_completion_history (
 execution_id TEXT NOT NULL REFERENCES executions(id), generation INTEGER NOT NULL,
 receipt TEXT NOT NULL, PRIMARY KEY(execution_id,generation)
);
