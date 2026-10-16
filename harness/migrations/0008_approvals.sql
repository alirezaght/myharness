CREATE TABLE approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    spec_version INTEGER NOT NULL,
    path TEXT NOT NULL,
    revision TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    review_state_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, spec_version),
    FOREIGN KEY (task_id, spec_version) REFERENCES spec_versions(task_id, version)
);
CREATE INDEX idx_approvals_task ON approvals(task_id, id);
