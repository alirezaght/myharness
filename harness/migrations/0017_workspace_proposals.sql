CREATE TABLE workspace_proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    base TEXT NOT NULL,
    source_json TEXT NOT NULL,
    operations_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, request_id)
);
CREATE INDEX workspace_proposals_task ON workspace_proposals(task_id, created_at);
