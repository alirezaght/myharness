CREATE TABLE design_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    section TEXT NOT NULL CHECK (section IN ('components', 'responsibilities', 'interactions',
        'data_flow', 'interfaces', 'configuration', 'failure_handling', 'required_behaviour')),
    kind TEXT NOT NULL CHECK (kind IN ('element', 'not_applicable')),
    origin TEXT NOT NULL CHECK (origin IN ('developer', 'recommendation')),
    binding TEXT NOT NULL CHECK (binding IN ('fixed', 'delegated')),
    statement TEXT NOT NULL,
    rationale TEXT,
    status TEXT NOT NULL CHECK (status IN ('stated', 'pending', 'accepted', 'rejected',
        'modified', 'withdrawn')),
    response TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX idx_design_entries_task ON design_entries(task_id, id);
