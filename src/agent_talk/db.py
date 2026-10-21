"""SQLite storage for projects, tasks, messages, events and approvals."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
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
"""

# Bounded history: a long task should not grow the database without limit.
MAX_EVENTS_PER_TASK = 500


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def close(self):
        self.conn.close()

    def query(self, sql: str, *args) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.conn.execute(sql, args)]

    def one(self, sql: str, *args) -> dict[str, Any] | None:
        rows = self.query(sql, *args)
        return rows[0] if rows else None

    def execute(self, sql: str, *args) -> int:
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    # Projects

    def add_project(self, name: str, path: str, aliases: list[str], agent: str) -> dict:
        project_id = self.execute(
            "INSERT INTO projects (name, path, aliases, agent, created_at) VALUES (?, ?, ?, ?, ?)",
            name, path, json.dumps(aliases), agent, time.time())
        return self.project(project_id)

    def project(self, project_id: int) -> dict | None:
        return _project(self.one("SELECT * FROM projects WHERE id = ?", project_id))

    def projects(self) -> list[dict]:
        return [_project(row) for row in self.query("SELECT * FROM projects ORDER BY name")]

    def update_project(self, project_id: int, **fields):
        if "aliases" in fields:
            fields["aliases"] = json.dumps(fields["aliases"])
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.execute(f"UPDATE projects SET {assignments} WHERE id = ?", *fields.values(), project_id)

    def remove_project(self, project_id: int):
        self.execute("DELETE FROM projects WHERE id = ?", project_id)

    # Tasks

    def add_task(self, project_id: int, agent: str, title: str) -> int:
        now = time.time()
        return self.execute(
            "INSERT INTO tasks (project_id, agent, title, status, created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?)",
            project_id, agent, title, now, now)

    def task(self, task_id: int) -> dict | None:
        return self.one("SELECT * FROM tasks WHERE id = ?", task_id)

    def tasks(self, limit: int = 100) -> list[dict]:
        return self.query("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", limit)

    def update_task(self, task_id: int, **fields):
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", *fields.values(), task_id)

    # Messages and events

    def add_message(self, task_id: int, role: str, text: str, state: str) -> int:
        return self.execute("INSERT INTO task_messages (task_id, role, text, state, created_at) VALUES (?, ?, ?, ?, ?)",
                            task_id, role, text, state, time.time())

    def messages(self, task_id: int) -> list[dict]:
        return self.query("SELECT * FROM task_messages WHERE task_id = ? ORDER BY id", task_id)

    def next_queued_message(self, task_id: int) -> dict | None:
        return self.one("SELECT * FROM task_messages WHERE task_id = ? AND state = 'queued' ORDER BY id LIMIT 1", task_id)

    def set_message_state(self, message_id: int, state: str):
        self.execute("UPDATE task_messages SET state = ? WHERE id = ?", state, message_id)

    def add_event(self, task_id: int, kind: str, data: dict) -> int:
        event_id = self.execute("INSERT INTO task_events (task_id, kind, data, created_at) VALUES (?, ?, ?, ?)",
                                task_id, kind, json.dumps(data), time.time())
        self.execute("DELETE FROM task_events WHERE task_id = ? AND id <= ?", task_id, event_id - MAX_EVENTS_PER_TASK)
        return event_id

    def events(self, task_id: int) -> list[dict]:
        rows = self.query("SELECT * FROM task_events WHERE task_id = ? ORDER BY id", task_id)
        for row in rows:
            row["data"] = json.loads(row["data"])
        return rows

    # Approvals

    def add_approval(self, task_id: int, kind: str, risk: str, summary: str, detail: dict) -> int:
        return self.execute(
            "INSERT INTO approvals (task_id, kind, risk, summary, detail, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            task_id, kind, risk, summary, json.dumps(detail), time.time())

    def approval(self, approval_id: int) -> dict | None:
        return _approval(self.one("SELECT * FROM approvals WHERE id = ?", approval_id))

    def approvals(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.query("SELECT * FROM approvals WHERE status = ? ORDER BY id", status)
        else:
            rows = self.query("SELECT * FROM approvals ORDER BY id DESC LIMIT 200")
        return [_approval(row) for row in rows]

    def decide_approval(self, approval_id: int, status: str, decided_by: str):
        self.execute("UPDATE approvals SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                     status, decided_by, time.time(), approval_id)

    def add_rule(self, project_id: int, kind: str, pattern: str) -> int:
        self.execute("INSERT OR IGNORE INTO approval_rules (project_id, kind, pattern, created_at) VALUES (?, ?, ?, ?)",
                     project_id, kind, pattern, time.time())
        return self.one("SELECT id FROM approval_rules WHERE project_id = ? AND kind = ? AND pattern = ?",
                        project_id, kind, pattern)["id"]

    def rules(self, project_id: int | None = None) -> list[dict]:
        if project_id is None:
            return self.query("SELECT * FROM approval_rules ORDER BY project_id, kind, pattern")
        return self.query("SELECT * FROM approval_rules WHERE project_id = ? ORDER BY kind, pattern", project_id)

    def remove_rule(self, rule_id: int):
        self.execute("DELETE FROM approval_rules WHERE id = ?", rule_id)

    def recover(self):
        """After a restart: work that was in flight has an unknown outcome."""
        now = time.time()
        self.execute("UPDATE tasks SET status = 'unknown', updated_at = ? WHERE status IN ('running', 'awaiting_approval')", now)
        self.execute("UPDATE approvals SET status = 'cancelled', decided_by = 'restart', decided_at = ? WHERE status = 'pending'", now)
        self.execute("UPDATE task_messages SET state = 'done' WHERE state = 'sent'")


def _project(row: dict | None) -> dict | None:
    if row is not None:
        row["aliases"] = json.loads(row["aliases"])
    return row


def _approval(row: dict | None) -> dict | None:
    if row is not None:
        row["detail"] = json.loads(row["detail"])
    return row
