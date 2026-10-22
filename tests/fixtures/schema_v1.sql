-- The schema before version 2 (agent-talk M1), for migration tests.

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    path TEXT NOT NULL UNIQUE,
    aliases TEXT NOT NULL DEFAULT '[]',
    agent TEXT NOT NULL DEFAULT 'codex',
    approval_mode TEXT,
    sandbox TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    thread_id TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS task_messages (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL,            -- user | agent
    text TEXT NOT NULL,
    state TEXT NOT NULL,           -- queued | sent | done
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS task_events_task ON task_events(task_id, id);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,            -- command | edit | permissions
    risk TEXT NOT NULL,            -- read | edit | execute | network | destructive
    summary TEXT NOT NULL,
    detail TEXT NOT NULL,
    status TEXT NOT NULL,          -- pending | approved | declined | cancelled
    decided_by TEXT,               -- user | rule:<id> | policy
    created_at REAL NOT NULL,
    decided_at REAL
);
CREATE TABLE IF NOT EXISTS approval_rules (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,            -- command | edit
    pattern TEXT NOT NULL,         -- command prefix; '*' for edits
    created_at REAL NOT NULL,
    UNIQUE(project_id, kind, pattern)
);

