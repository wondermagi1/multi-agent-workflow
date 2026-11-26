"""SQLite event storage with atomic snapshots and version checks."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class VersionConflict(Exception):
    """The requested base version is no longer current."""


class RunStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    snapshot TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq),
                    FOREIGN KEY (run_id) REFERENCES runs(id)
                );
                CREATE TABLE IF NOT EXISTS commands (
                    run_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    PRIMARY KEY (run_id, command_id),
                    FOREIGN KEY (run_id) REFERENCES runs(id)
                );
            """)

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def list_runs(self):
        with self._connection() as conn:
            rows = conn.execute("SELECT snapshot FROM runs ORDER BY updated_at DESC, id DESC")
            return [json.loads(row[0]) for row in rows]

    def get(self, run_id, at=None):
        with self._connection() as conn:
            if at is None:
                row = conn.execute("SELECT snapshot FROM runs WHERE id=?", (run_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT snapshot FROM events WHERE run_id=? AND seq=?", (run_id, at)
                ).fetchone()
            return json.loads(row[0]) if row else None

    def events(self, run_id, after=0, through=None):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT event FROM events WHERE run_id=? AND seq>? AND (? IS NULL OR seq<=?) ORDER BY seq",
                (run_id, after, through, through),
            )
            return [json.loads(row[0]) for row in rows]

    def receipt(self, run_id, command_id):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT action FROM commands WHERE run_id=? AND command_id=?", (run_id, command_id)
            ).fetchone()
            return row[0] if row else None

    def append(self, state, event_type, title, details=None, command_id=None, action=None):
        now = utcnow()
        seq = state["version"] + 1
        snapshot = {**state, "version": seq, "updated_at": now}
        event = {
            "seq": seq,
            "type": event_type,
            "title": title,
            "timestamp": now,
            "stage": state["stage"],
            "node": state["node"],
            "details": details or {},
        }
        run_id = state["id"]
        encoded_snapshot = json.dumps(snapshot, ensure_ascii=False)
        encoded_event = json.dumps(event, ensure_ascii=False)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if seq == 1:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO runs(id, version, updated_at, snapshot) VALUES (?, ?, ?, ?)",
                    (run_id, seq, now, encoded_snapshot),
                )
            else:
                cursor = conn.execute(
                    "UPDATE runs SET version=?, updated_at=?, snapshot=? WHERE id=? AND version=?",
                    (seq, now, encoded_snapshot, run_id, state["version"]),
                )
            if cursor.rowcount != 1:
                raise VersionConflict()
            conn.execute(
                "INSERT INTO events(run_id, seq, event, snapshot) VALUES (?, ?, ?, ?)",
                (run_id, seq, encoded_event, encoded_snapshot),
            )
            if command_id is not None:
                conn.execute(
                    "INSERT INTO commands(run_id, command_id, action) VALUES (?, ?, ?)",
                    (run_id, command_id, action),
                )
        state.update(version=seq, updated_at=now)
        return event
