CREATE TABLE comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    diagram_version INTEGER NOT NULL,
    element_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('clarification', 'correction', 'redesign')),
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'addressed', 'resolved')),
    request_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (task_id, request_id),
    FOREIGN KEY (task_id, diagram_version) REFERENCES diagram_versions(task_id, version)
);
CREATE INDEX idx_comments_task ON comments(task_id, id);
