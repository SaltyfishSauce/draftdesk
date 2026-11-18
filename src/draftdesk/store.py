"""SQLite persistence: draft queue, audit log, and polling cursors."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    message_id TEXT NOT NULL,
    status TEXT NOT NULL,
    customer_email TEXT DEFAULT '',
    customer_name TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    category TEXT DEFAULT '',
    language TEXT DEFAULT '',
    draft_body TEXT DEFAULT '',
    final_body TEXT DEFAULT '',
    reviewer_notes TEXT DEFAULT '',
    flags TEXT DEFAULT '[]',
    inbound TEXT DEFAULT '{}',
    customer TEXT DEFAULT '{}',
    orders TEXT DEFAULT '[]',
    model TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    provider_draft_id TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT DEFAULT '',
    UNIQUE(provider, message_id)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cursors (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS drafts_status ON drafts(status, id);
"""

STATUSES = ("pending", "in_mailbox", "sent", "rejected", "skipped", "error")
_JSON_FIELDS = ("flags", "inbound", "customer", "orders")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One connection shared across the web server's worker threads, guarded
        # by a lock. Volume here is a support inbox, not a message bus.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        for field in _JSON_FIELDS:
            data[field] = json.loads(data[field] or "null")
        return data

    def seen(self, provider: str, message_id: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "SELECT 1 FROM drafts WHERE provider=? AND message_id=?", (provider, message_id)
            )
            return cur.fetchone() is not None

    def add(self, provider: str, message_id: str, status: str, **fields: Any) -> int:
        assert status in STATUSES, status
        for field in _JSON_FIELDS:
            if field in fields:
                fields[field] = json.dumps(fields[field], ensure_ascii=False, default=str)
        columns = ["provider", "message_id", "status", "created_at", *fields]
        values = [provider, message_id, status, _now(), *fields.values()]
        marks = ",".join("?" * len(columns))
        with self._lock:
            cur = self._db.execute(
                f"INSERT INTO drafts ({','.join(columns)}) VALUES ({marks})", values
            )
            self._db.commit()
            return int(cur.lastrowid)

    def update(self, draft_id: int, **fields: Any) -> None:
        if "status" in fields:
            assert fields["status"] in STATUSES, fields["status"]
        for field in _JSON_FIELDS:
            if field in fields:
                fields[field] = json.dumps(fields[field], ensure_ascii=False, default=str)
        sets = ",".join(f"{k}=?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE drafts SET {sets} WHERE id=?", [*fields.values(), draft_id])
            self._db.commit()

    def claim(self, draft_id: int, from_status: str, to_status: str) -> bool:
        """Atomically move a draft between statuses.

        Guards the send path: two reviewers clicking Approve at the same moment
        must not both send the reply.
        """
        with self._lock:
            cur = self._db.execute(
                "UPDATE drafts SET status=?, decided_at=? WHERE id=? AND status=?",
                (to_status, _now(), draft_id, from_status),
            )
            self._db.commit()
            return cur.rowcount == 1

    def get(self, draft_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return self._row(row)

    def list(self, status: str, limit: int = 100) -> list[dict[str, Any]]:
        order = "ASC" if status == "pending" else "DESC"
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM drafts WHERE status=? ORDER BY id {order} LIMIT ?", (status, limit)
            ).fetchall()
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT status, COUNT(*) AS n FROM drafts GROUP BY status"
            ).fetchall()
        counts = {s: 0 for s in STATUSES}
        counts.update({r["status"]: r["n"] for r in rows})
        return counts

    def audit(self, draft_id: int | None, actor: str, action: str, detail: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO audit (draft_id, at, actor, action, detail) VALUES (?,?,?,?,?)",
                (draft_id, _now(), actor, action, detail),
            )
            self._db.commit()

    def audit_for(self, draft_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM audit WHERE draft_id=? ORDER BY id", (draft_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_cursor(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM cursors WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_cursor(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO cursors (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._db.commit()
