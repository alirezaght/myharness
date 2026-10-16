ALTER TABLE proposals ADD COLUMN impact TEXT;
CREATE TABLE decision_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    decision_id TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    rationale TEXT NOT NULL,
    scope TEXT NOT NULL,
    impact TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected', 'modified')),
    response TEXT,
    result_path TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX idx_decision_changes_task ON decision_changes(task_id, id);
