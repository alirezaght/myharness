CREATE TABLE test_scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    category TEXT NOT NULL CHECK (category IN ('unit', 'integration', 'e2e', 'regression',
        'edge_case', 'failure')),
    origin TEXT NOT NULL CHECK (origin IN ('developer', 'recommendation')),
    statement TEXT NOT NULL,
    rationale TEXT,
    covers TEXT NOT NULL DEFAULT '[]',
    mock TEXT,
    mock_justification TEXT,
    concern TEXT,
    status TEXT NOT NULL CHECK (status IN ('stated', 'pending', 'accepted', 'rejected',
        'modified', 'withdrawn')),
    response TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK ((mock IS NULL) = (mock_justification IS NULL))
);
CREATE INDEX idx_test_scenarios_task ON test_scenarios(task_id, id);
CREATE TABLE test_plan_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    content_hash TEXT NOT NULL,
    scenario_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_test_plan_approvals_task ON test_plan_approvals(task_id, id);
