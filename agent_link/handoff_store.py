"""Durable local handoff records; this store does not dispatch Multica tasks."""

from __future__ import annotations

import contextlib
import json
import secrets
import sqlite3
import time
from pathlib import Path


class HandoffError(RuntimeError):
    pass


class HandoffStore:
    """Persist one execution per verified retry lineage, including uncertain outcomes."""

    def __init__(self, path: Path):
        self.path = str(path)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS handoffs ("
                "lineage TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, "
                "state TEXT NOT NULL, handle TEXT, result TEXT, updated REAL NOT NULL)"
            )

    @contextlib.contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, lineage: str, task_id: str, payload: dict) -> str:
        """The caller must independently verify the root of the retry lineage."""
        if not lineage or not task_id or not isinstance(payload, dict):
            raise HandoffError("A verified lineage, task ID and payload are required")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM handoffs WHERE lineage=? OR task_id=?", (lineage, task_id)).fetchone()
            if row:
                if row["lineage"] == lineage and row["task_id"] == task_id and row["payload"] == encoded:
                    return row["state"]
                raise HandoffError("Task or retry lineage already has a handoff; inspect it before proceeding")
            db.execute(
                "INSERT INTO handoffs VALUES (?, ?, ?, 'pending', NULL, NULL, ?)",
                (lineage, task_id, encoded, time.time()),
            )
        return "pending"

    def claim(self, lineage: str) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
            if not row or row["state"] != "pending":
                raise HandoffError("Handoff is missing or already claimed; inspect it before proceeding")
            handle = secrets.token_urlsafe(32)
            db.execute(
                "UPDATE handoffs SET state='claimed', handle=?, updated=? WHERE lineage=?",
                (handle, time.time(), lineage),
            )
            return {"task_id": row["task_id"], "handle": handle, "payload": json.loads(row["payload"])}

    def submit(self, lineage: str, handle: str, result: dict) -> None:
        if not isinstance(result, dict) or not result:
            raise HandoffError("A nonempty candidate result is required")
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
            if (
                not row
                or not row["handle"]
                or not isinstance(handle, str)
                or not secrets.compare_digest(row["handle"], handle)
            ):
                raise HandoffError("Invalid handoff handle")
            if row["state"] == "submitted" and row["result"] == encoded:
                return
            if row["state"] != "claimed":
                raise HandoffError("Handoff is not accepting a result")
            db.execute(
                "UPDATE handoffs SET state='submitted', result=?, updated=? WHERE lineage=?",
                (encoded, time.time(), lineage),
            )

    def inspect(self, lineage: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT task_id, state, result, updated FROM handoffs WHERE lineage=?", (lineage,)
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "result": json.loads(row["result"]) if row["result"] else None}
