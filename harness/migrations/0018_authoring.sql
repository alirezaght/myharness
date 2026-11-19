CREATE TABLE authoring_tasks (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), project TEXT NOT NULL,
 generation INTEGER NOT NULL DEFAULT 0, working_id TEXT, adopted_id TEXT,
 FOREIGN KEY(task_id,working_id) REFERENCES authoring_revisions(task_id,id),
 FOREIGN KEY(task_id,adopted_id) REFERENCES authoring_revisions(task_id,id)
);
CREATE TABLE authoring_graphs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), graph_json TEXT NOT NULL,
 digest TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id,id)
);
CREATE TABLE authoring_revisions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), parent_id TEXT,
 body TEXT NOT NULL, body_hash TEXT NOT NULL, graph_id TEXT, source_json TEXT NOT NULL,
 source_digest TEXT NOT NULL, attribution TEXT NOT NULL, obligations_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(task_id,id),
 FOREIGN KEY(task_id,parent_id) REFERENCES authoring_revisions(task_id,id),
 FOREIGN KEY(task_id,graph_id) REFERENCES authoring_graphs(task_id,id)
);
CREATE TABLE authoring_contributions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL,
 actor TEXT NOT NULL, payload_json TEXT NOT NULL, supersedes TEXT, created_at TEXT NOT NULL,
 UNIQUE(task_id,id), FOREIGN KEY(task_id,supersedes) REFERENCES authoring_contributions(task_id,id)
);
CREATE TABLE authoring_jobs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), project TEXT NOT NULL,
 kind TEXT NOT NULL, host TEXT NOT NULL, input_json TEXT NOT NULL, state TEXT NOT NULL,
 attempt INTEGER NOT NULL DEFAULT 1, claim INTEGER NOT NULL DEFAULT 0, owner TEXT,
 cancelled INTEGER NOT NULL DEFAULT 0, applied_id TEXT UNIQUE, result_json TEXT,
 error TEXT, settlement TEXT NOT NULL DEFAULT 'settled', created_at TEXT NOT NULL,
 UNIQUE(task_id,id)
);
CREATE TABLE authoring_attempts (
 job_id TEXT NOT NULL REFERENCES authoring_jobs(id), attempt INTEGER NOT NULL,
 claim INTEGER NOT NULL, owner TEXT, state TEXT NOT NULL, settlement TEXT NOT NULL,
 input_json TEXT NOT NULL, result_json TEXT, error TEXT,
 PRIMARY KEY(job_id,attempt)
);
CREATE TABLE authoring_receipts (
 scope TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
 result_json TEXT NOT NULL, PRIMARY KEY(scope,request_id)
);
CREATE TABLE authoring_projections (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), revision_id TEXT NOT NULL,
 state TEXT NOT NULL, FOREIGN KEY(task_id,revision_id) REFERENCES authoring_revisions(task_id,id)
);
CREATE INDEX authoring_jobs_queue ON authoring_jobs(state,created_at);
