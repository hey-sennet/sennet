"""SQLite storage for projects, tasks, messages, events, approvals, paired devices and their push subscriptions.

Task titles and messages are indexed for search (FTS5 with the trigram tokenizer, so part
of a word matches), kept in step by triggers, including when a task's rows are deleted.
"""
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
    agent TEXT NOT NULL DEFAULT 'codex',
    approval_mode TEXT,
    access TEXT,                   -- read-only | workspace | full; null for the default
    project_config INTEGER NOT NULL DEFAULT 0,  -- let the agent load the project's own hooks and servers
    model TEXT,                    -- the agent's model id for new tasks; null for the agent's default
    effort TEXT,                   -- reasoning effort; null for the model's default
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    resume_token TEXT,             -- the adapter's own id for continuing the conversation
    model TEXT,                    -- overrides the project's model; null to follow it
    effort TEXT,
    error TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,   -- summed over the task's turns; input includes cached
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
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
-- Search: a message's row is its message id, a title's its task id.
CREATE VIRTUAL TABLE IF NOT EXISTS search_messages USING fts5(text, tokenize = 'trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS search_titles USING fts5(title, tokenize = 'trigram');
CREATE TRIGGER IF NOT EXISTS task_messages_searched AFTER INSERT ON task_messages BEGIN
    INSERT INTO search_messages (rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS task_messages_unsearched AFTER DELETE ON task_messages BEGIN
    DELETE FROM search_messages WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS tasks_searched AFTER INSERT ON tasks BEGIN
    INSERT INTO search_titles (rowid, title) VALUES (new.id, new.title);
END;
CREATE TRIGGER IF NOT EXISTS tasks_retitled AFTER UPDATE OF title ON tasks BEGIN
    UPDATE search_titles SET title = new.title WHERE rowid = new.id;
END;
CREATE TRIGGER IF NOT EXISTS tasks_unsearched AFTER DELETE ON tasks BEGIN
    DELETE FROM search_titles WHERE rowid = old.id;
END;
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,            -- command | edit | tool | access
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
    kind TEXT NOT NULL,            -- command | edit | tool
    pattern TEXT NOT NULL,         -- command prefix; '*' for edits; tool name
    created_at REAL NOT NULL,
    UNIQUE(project_id, kind, pattern)
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,   -- SHA-256 of the device's token; the token itself is never stored
    paired_by TEXT NOT NULL,           -- "the terminal", or the name of the device that showed the code
    created_at REAL NOT NULL,
    last_seen_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pairing_codes (
    id INTEGER PRIMARY KEY,
    link_hash TEXT NOT NULL UNIQUE,    -- SHA-256 of the code in a pairing link
    typed_hash TEXT UNIQUE,            -- SHA-256 of the short code typed instead; either one redeems it
    created_by TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,            -- the browser's push service URL for this device
    p256dh TEXT NOT NULL,              -- the browser's public key and auth secret, base64url
    auth TEXT NOT NULL,
    kinds TEXT NOT NULL,               -- what notifies, space-separated: approval finished failed
    created_at REAL NOT NULL
);
"""

# Bounded history: a long task should not grow the database without limit.
MAX_EVENTS_PER_TASK = 500

SCHEMA_VERSION = 10

# A task that has ended, whose agent isn't working on it: these may be deleted, and are "Earlier".
FINISHED = ("completed", "failed", "cancelled", "unknown")

# Rows written before version 2 hold Codex's own words; these map them to the contract's.
_V2_ACCESS = {"read-only": "read-only", "workspace-write": "workspace", "danger-full-access": "full"}
_V2_ITEM_STATUS = {"inProgress": "running"}
_V2_STEP_STATUS = {"inProgress": "active", "completed": "done"}
_V2_TURN_STATUS = {"interrupted": "cancelled"}


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        version = self._migrate()
        self.conn.executescript(SCHEMA)
        if version is not None and version < 8:
            # The search index is new in version 8: fill it from what is already there.
            self.conn.execute("INSERT INTO search_messages (rowid, text) SELECT id, text FROM task_messages")
            self.conn.execute("INSERT INTO search_titles (rowid, title) SELECT id, title FROM tasks")
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.lock = threading.Lock()

    def _migrate(self) -> int | None:
        """Bring an older database up to date; its version before, or None for a new one."""
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'projects'").fetchone()
        if not exists:
            return None  # a new database gets the current schema as it stands
        if version >= SCHEMA_VERSION:
            return version
        self.conn.execute("BEGIN")
        try:
            if version < 2:
                self._to_v2()
            if version < 3:
                self._to_v3()
            if version < 4:
                self._to_v4()
            if version < 5:
                self._to_v5()
            # Version 6 only adds tables (devices, pairing_codes), which SCHEMA creates.
            if version < 7:
                self._to_v7()
            # Version 8 adds the search index (SCHEMA), filled once it exists.
            if version < 9:
                self._to_v9()
            # Version 10 only adds a table (push_subscriptions), which SCHEMA creates.
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return version

    def _to_v9(self):
        """Project aliases went with routing by a spoken project name."""
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(projects)")]
        if "aliases" in columns:
            self.conn.execute("ALTER TABLE projects DROP COLUMN aliases")

    def _to_v7(self):
        """Tokens used, per task. Without a tasks table yet, SCHEMA makes one with them."""
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'tasks'").fetchone():
            for column in ("input_tokens", "cached_tokens", "output_tokens"):
                self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")

    def _to_v5(self):
        """Model and effort, per project and per task."""
        for table in ("projects", "tasks"):
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN model TEXT")
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN effort TEXT")

    def _to_v4(self):
        self.conn.execute("ALTER TABLE projects ADD COLUMN project_config INTEGER NOT NULL DEFAULT 0")

    def _to_v3(self):
        """Codex's "permissions" approval kind is the contract's "access"."""
        self.conn.execute("UPDATE approvals SET kind = 'access' WHERE kind = 'permissions'")

    def _to_v2(self):
        """Provider-neutral names: access levels, resume tokens and event statuses."""
        self.conn.execute("ALTER TABLE projects RENAME COLUMN sandbox TO access")
        for old, new in _V2_ACCESS.items():
            self.conn.execute("UPDATE projects SET access = ? WHERE access = ?", (new, old))
        self.conn.execute("ALTER TABLE tasks RENAME COLUMN thread_id TO resume_token")
        rows = self.conn.execute("SELECT id, kind, data FROM task_events WHERE kind IN "
                                 "('command_finished', 'file_change', 'plan', 'turn_completed')").fetchall()
        for row in rows:
            data = json.loads(row["data"])
            if row["kind"] in ("command_finished", "file_change"):
                data["status"] = _V2_ITEM_STATUS.get(data.get("status"), data.get("status"))
            elif row["kind"] == "plan":
                for step in data.get("steps") or []:
                    step["status"] = _V2_STEP_STATUS.get(step.get("status"), step.get("status"))
            else:
                data["status"] = _V2_TURN_STATUS.get(data.get("status"), data.get("status"))
            self.conn.execute("UPDATE task_events SET data = ? WHERE id = ?", (json.dumps(data), row["id"]))

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

    def add_project(self, name: str, path: str, agent: str) -> dict:
        project_id = self.execute(
            "INSERT INTO projects (name, path, agent, created_at) VALUES (?, ?, ?, ?)",
            name, path, agent, time.time())
        return self.project(project_id)

    def project(self, project_id: int) -> dict | None:
        return _project(self.one("SELECT * FROM projects WHERE id = ?", project_id))

    def projects(self) -> list[dict]:
        return [_project(row) for row in self.query("SELECT * FROM projects ORDER BY name")]

    def update_project(self, project_id: int, **fields):
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.execute(f"UPDATE projects SET {assignments} WHERE id = ?", *fields.values(), project_id)

    def remove_project(self, project_id: int):
        self.execute("DELETE FROM projects WHERE id = ?", project_id)

    # Tasks

    def add_task(self, project_id: int, agent: str, title: str, model: str | None = None,
                 effort: str | None = None) -> int:
        now = time.time()
        return self.execute(
            "INSERT INTO tasks (project_id, agent, title, model, effort, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)", project_id, agent, title, model, effort, now, now)

    def task(self, task_id: int) -> dict | None:
        return self.one("SELECT * FROM tasks WHERE id = ?", task_id)

    def tasks(self, limit: int = 100) -> list[dict]:
        return self.query("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", limit)

    def current_tasks(self, earlier: int) -> tuple[list[dict], bool]:
        """Every task not yet finished, and the `earlier` most recently finished; and whether there are more."""
        marks = ", ".join("?" * len(FINISHED))
        current = self.query(f"SELECT * FROM tasks WHERE status NOT IN ({marks}) ORDER BY updated_at DESC", *FINISHED)
        finished = self.query(f"SELECT * FROM tasks WHERE status IN ({marks}) ORDER BY updated_at DESC, id DESC LIMIT ?",
                              *FINISHED, earlier + 1)
        return current + finished[:earlier], len(finished) > earlier

    def earlier_tasks(self, before: float, before_id: int, limit: int) -> tuple[list[dict], bool]:
        """Finished tasks older than the one given (by `updated_at`, then id); and whether there are more."""
        marks = ", ".join("?" * len(FINISHED))
        rows = self.query(f"SELECT * FROM tasks WHERE status IN ({marks}) AND (updated_at < ? OR (updated_at = ? AND id < ?)) "
                          "ORDER BY updated_at DESC, id DESC LIMIT ?", *FINISHED, before, before, before_id, limit + 1)
        return rows[:limit], len(rows) > limit

    def delete_task(self, task_id: int):
        """With its messages, events and approvals, and its place in the search index."""
        self.execute("DELETE FROM tasks WHERE id = ?", task_id)

    def search(self, query: str, limit: int) -> list[tuple[dict, str]]:
        """Tasks whose title or messages hold every word of `query` (three letters or more, any
        part of a word), best first, each with the text it matched in."""
        words = [w for w in query.lower().split() if len(w) >= 3]
        if not words:
            return []
        match = " AND ".join('"' + w.replace('"', '""') + '"' for w in words)
        # bm25 is negative, better matches lower; a title match counts as more than one message.
        rows = self.query(
            "SELECT task_id, text, MIN(rank) AS rank FROM ("
            " SELECT rowid AS task_id, title AS text, bm25(search_titles) * 2 AS rank FROM search_titles WHERE search_titles MATCH ?"
            " UNION ALL"
            " SELECT m.task_id, s.text, bm25(search_messages) AS rank FROM search_messages s"
            " JOIN task_messages m ON m.id = s.rowid WHERE search_messages MATCH ?"
            ") GROUP BY task_id ORDER BY rank LIMIT ?", match, match, limit)
        return [(task, row["text"]) for row in rows if (task := self.task(row["task_id"]))]

    def search_candidates(self, query: str, limit: int) -> list[tuple[dict, str]]:
        """Messages and titles sharing any three letters in a row with `query`, for a fuzzy match
        to rank: those with the most in common come first, but not all of them are close."""
        grams = sorted({w[i:i + 3] for w in query.lower().split() if len(w) >= 3 for i in range(len(w) - 2)})
        if not grams:
            return []
        match = " OR ".join('"' + g.replace('"', '""') + '"' for g in grams)
        rows = self.query(
            "SELECT rowid AS task_id, title AS text, bm25(search_titles) AS rank FROM search_titles WHERE search_titles MATCH ?"
            " UNION ALL"
            " SELECT m.task_id, s.text, bm25(search_messages) AS rank FROM search_messages s"
            " JOIN task_messages m ON m.id = s.rowid WHERE search_messages MATCH ? ORDER BY rank LIMIT ?", match, match, limit)
        tasks: dict[int, dict | None] = {}
        found = []
        for row in rows:
            if row["task_id"] not in tasks:
                tasks[row["task_id"]] = self.task(row["task_id"])
            if tasks[row["task_id"]]:
                found.append((tasks[row["task_id"]], row["text"]))
        return found

    def update_task(self, task_id: int, **fields):
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", *fields.values(), task_id)

    def add_usage(self, task_id: int, input: int, cached: int, output: int):
        """A turn's tokens, added to the task's; not a change to the task, so `updated_at` stays."""
        self.execute("UPDATE tasks SET input_tokens = input_tokens + ?, cached_tokens = cached_tokens + ?, "
                     "output_tokens = output_tokens + ? WHERE id = ?", input, cached, output, task_id)

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

    # Devices and pairing codes (access.py)

    def add_pairing(self, link_hash: str, typed_hash: str | None, created_by: str, expires_at: float):
        self.execute("DELETE FROM pairing_codes WHERE expires_at < ?", time.time())
        self.execute("INSERT INTO pairing_codes (link_hash, typed_hash, created_by, expires_at) VALUES (?, ?, ?, ?)",
                     link_hash, typed_hash, created_by, expires_at)

    def take_pairing(self, code_hash: str) -> dict | None:
        """The unexpired pairing code with this hash, in either form, deleted so it works once."""
        with self.lock:
            row = self.conn.execute(
                "DELETE FROM pairing_codes WHERE (link_hash = ? OR typed_hash = ?) AND expires_at >= ? RETURNING *",
                (code_hash, code_hash, time.time())).fetchone()
        return dict(row) if row else None

    def add_device(self, name: str, token_hash: str, paired_by: str) -> dict:
        now = time.time()
        device_id = self.execute(
            "INSERT INTO devices (name, token_hash, paired_by, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
            name, token_hash, paired_by, now, now)
        return self.device(device_id)

    def device(self, device_id: int) -> dict | None:
        return self.one("SELECT id, name, paired_by, created_at, last_seen_at FROM devices WHERE id = ?", device_id)

    def device_by_token(self, token_hash: str) -> dict | None:
        return self.one("SELECT id, name, paired_by, created_at, last_seen_at FROM devices WHERE token_hash = ?",
                        token_hash)

    def devices(self) -> list[dict]:
        return self.query("SELECT id, name, paired_by, created_at, last_seen_at FROM devices ORDER BY created_at")

    def update_device(self, device_id: int, **fields):
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.execute(f"UPDATE devices SET {assignments} WHERE id = ?", *fields.values(), device_id)

    def remove_device(self, device_id: int):
        self.execute("DELETE FROM devices WHERE id = ?", device_id)

    # Push subscriptions (push.py), one per device

    def set_push(self, device_id: int, endpoint: str, p256dh: str, auth: str, kinds: list[str]):
        self.execute("INSERT INTO push_subscriptions (device_id, endpoint, p256dh, auth, kinds, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (device_id) DO UPDATE SET endpoint = excluded.endpoint, "
                     "p256dh = excluded.p256dh, auth = excluded.auth, kinds = excluded.kinds",
                     device_id, endpoint, p256dh, auth, " ".join(kinds), time.time())

    def push(self, device_id: int) -> dict | None:
        return _push(self.one("SELECT * FROM push_subscriptions WHERE device_id = ?", device_id))

    def pushes(self) -> list[dict]:
        return [_push(row) for row in self.query("SELECT * FROM push_subscriptions")]

    def remove_push(self, device_id: int, endpoint: str | None = None):
        """This device's subscription; only if it is still `endpoint`, when given."""
        if endpoint is None:
            self.execute("DELETE FROM push_subscriptions WHERE device_id = ?", device_id)
        else:
            self.execute("DELETE FROM push_subscriptions WHERE device_id = ? AND endpoint = ?", device_id, endpoint)

    def recover(self):
        """After a restart: work that was in flight has an unknown outcome."""
        now = time.time()
        self.execute("UPDATE tasks SET status = 'unknown', updated_at = ? WHERE status IN ('running', 'awaiting_approval', 'background')", now)
        self.execute("UPDATE approvals SET status = 'cancelled', decided_by = 'restart', decided_at = ? WHERE status = 'pending'", now)
        self.execute("UPDATE task_messages SET state = 'done' WHERE state = 'sent'")


def _project(row: dict | None) -> dict | None:
    if row is not None:
        row["project_config"] = bool(row["project_config"])
    return row


def _push(row: dict | None) -> dict | None:
    if row is not None:
        row["kinds"] = row["kinds"].split()
    return row


def _approval(row: dict | None) -> dict | None:
    if row is not None:
        row["detail"] = json.loads(row["detail"])
    return row
