CREATE TABLE diagram_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    model TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, version)
);
