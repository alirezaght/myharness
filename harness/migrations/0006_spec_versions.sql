CREATE TABLE spec_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    version INTEGER NOT NULL,
    path TEXT NOT NULL,
    revision TEXT NOT NULL,
    working_tree TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('approved', 'superseded')),
    superseded_by INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, version)
);
CREATE INDEX idx_spec_versions_task ON spec_versions(task_id, id);
