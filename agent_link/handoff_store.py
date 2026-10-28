"""Durable local handoff records; this store does not dispatch Multica tasks."""

from __future__ import annotations

import contextlib
import json
import os
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
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS handoffs ("
                "lineage TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, "
                "state TEXT NOT NULL, handle TEXT, result TEXT, updated REAL NOT NULL, lease_until REAL, workspace_key TEXT)"
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(handoffs)")}
            if "lease_until" not in columns:
                db.execute("ALTER TABLE handoffs ADD COLUMN lease_until REAL")
            if "workspace_key" not in columns:
                db.execute("ALTER TABLE handoffs ADD COLUMN workspace_key TEXT")
                for row in db.execute("SELECT lineage, payload FROM handoffs").fetchall():
                    payload = json.loads(row["payload"])
                    if isinstance(payload, dict):
                        db.execute(
                            "UPDATE handoffs SET workspace_key=? WHERE lineage=?",
                            (self._workspace_key(payload), row["lineage"]),
                        )
            existing_workspaces = [
                Path(row["workspace_key"])
                for row in db.execute("SELECT workspace_key FROM handoffs WHERE workspace_key IS NOT NULL")
            ]
            if any(
                self._overlap(a, b)
                for index, a in enumerate(existing_workspaces)
                for b in existing_workspaces[index + 1 :]
            ):
                raise HandoffError("Existing handoffs have overlapping workspaces; inspect them before startup")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS handoffs_workspace ON handoffs(workspace_key)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('instance_id', ?)", (secrets.token_hex(24),))

    def instance_id(self) -> str:
        with self._connect() as db:
            return db.execute("SELECT value FROM metadata WHERE key='instance_id'").fetchone()["value"]

    @staticmethod
    def _state(row, now: float) -> str:
        if row["state"] in ("pending", "claimed") and (row["lease_until"] is None or row["lease_until"] <= now):
            return "expired"
        return row["state"]

    @staticmethod
    def _workspace_key(payload: dict) -> str | None:
        workspace = payload.get("workspace")
        if not isinstance(workspace, str) or not Path(workspace).is_absolute():
            return None
        return os.path.normcase(str(Path(workspace).resolve()))

    @staticmethod
    def _overlap(a: Path, b: Path) -> bool:
        return a == b or a in b.parents or b in a.parents

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
                    return self._state(row, time.time())
                raise HandoffError("Task or retry lineage already has a handoff; inspect it before proceeding")
            workspace_key = self._workspace_key(payload)
            if workspace_key:
                candidate = Path(workspace_key)
                for existing in db.execute("SELECT workspace_key FROM handoffs WHERE workspace_key IS NOT NULL"):
                    previous = Path(existing["workspace_key"])
                    if self._overlap(candidate, previous):
                        raise HandoffError("Workspace overlaps another handoff; use a separate workspace")
            db.execute(
                "INSERT INTO handoffs (lineage, task_id, payload, state, updated, lease_until, workspace_key) "
                "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                (lineage, task_id, encoded, (now := time.time()), now + 60, workspace_key),
            )
        return "pending"

    def claim(self, lineage: str) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
            if not row or self._state(row, time.time()) != "pending":
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
                or not secrets.compare_digest(row["handle"].encode(), handle.encode())
            ):
                raise HandoffError("Invalid handoff handle")
            if row["state"] == "submitted" and row["result"] == encoded:
                return
            if self._state(row, time.time()) != "claimed":
                raise HandoffError("Handoff is not accepting a result")
            db.execute(
                "UPDATE handoffs SET state='submitted', result=?, updated=? WHERE lineage=?",
                (encoded, time.time(), lineage),
            )

    def inspect(self, lineage: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT task_id, state, result, updated, lease_until FROM handoffs WHERE lineage=?", (lineage,)
            ).fetchone()
        if row is None:
            return None
        return {
            **dict(row),
            "state": self._state(row, time.time()),
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    def submitted_workspace(self, lineage: str) -> str:
        with self._connect() as db:
            row = db.execute("SELECT state, payload FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
        if not row or row["state"] != "submitted":
            raise HandoffError("A submitted candidate is required")
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict) or not isinstance(payload.get("workspace"), str):
            raise HandoffError("The stored workspace is unavailable")
        return payload["workspace"]

    def pending(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT lineage, task_id, updated FROM handoffs WHERE state='pending' AND lease_until>? ORDER BY updated, lineage",
                (time.time(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def desktop_state(self, lineage: str, handle: str) -> str:
        with self._connect() as db:
            row = db.execute("SELECT state, handle, lease_until FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
        if (
            not row
            or not row["handle"]
            or not isinstance(handle, str)
            or not secrets.compare_digest(row["handle"].encode(), handle.encode())
        ):
            raise HandoffError("Invalid handoff handle")
        return self._state(row, time.time())

    def renew(self, lineage: str, task_id: str) -> float:
        """Renew only while the host has verified the original Multica run is active."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM handoffs WHERE lineage=? AND task_id=?", (lineage, task_id)).fetchone()
            now = time.time()
            if not row or self._state(row, now) not in ("pending", "claimed"):
                raise HandoffError("Lease expired or task is not active; inspect before any recovery")
            deadline = now + 60
            db.execute("UPDATE handoffs SET lease_until=?, updated=? WHERE lineage=?", (deadline, now, lineage))
        return deadline

    def expire(self, lineage: str) -> None:
        """Invalidate an active lease without erasing the task or permitting a retry."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE handoffs SET lease_until=0, updated=? WHERE lineage=? AND state IN ('pending', 'claimed')",
                (time.time(), lineage),
            )

    def cancel(self, lineage: str) -> str:
        """Stop accepting submissions without assuming an already claimed session stopped."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
            if row is None:
                raise HandoffError("Unknown handoff")
            if row["state"] == "cancelled":
                return "cancelled"
            if row["state"] not in ("pending", "claimed"):
                raise HandoffError("Handoff is already submitted")
            db.execute("UPDATE handoffs SET state='cancelled', updated=? WHERE lineage=?", (time.time(), lineage))
        return "cancelled"
