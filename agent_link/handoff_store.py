"""Durable local handoff records; this store does not dispatch Multica tasks."""

from __future__ import annotations

import contextlib
import json
import os
import re
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
            for column in ("recipient", "consumer_id", "request_id"):
                if column not in columns:
                    db.execute(f"ALTER TABLE handoffs ADD COLUMN {column} TEXT")
                    if column == "recipient":
                        for row in db.execute("SELECT lineage, payload FROM handoffs").fetchall():
                            recipient = json.loads(row["payload"]).get("recipient")
                            if recipient is not None:
                                self._identifier(recipient, "recipient")
                                db.execute(
                                    "UPDATE handoffs SET recipient=? WHERE lineage=?", (recipient, row["lineage"])
                                )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS handoffs_request ON handoffs(consumer_id, request_id) "
                "WHERE consumer_id IS NOT NULL"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS handoff_messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, lineage TEXT NOT NULL, sender TEXT NOT NULL, "
                "message_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL, "
                "UNIQUE(lineage, sender, message_id))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS handoff_message_cursor ON handoff_messages(lineage, id)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS handoff_notifications ("
                "lineage TEXT NOT NULL, session_id TEXT NOT NULL, state TEXT NOT NULL, "
                "PRIMARY KEY(lineage,session_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS handoff_listeners ("
                "consumer_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, waiting_until REAL NOT NULL)"
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

    def begin_notification(self, lineage: str, session_id: str) -> dict:
        self._identifier(session_id, "session_id")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT * FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
            if not task or self._state(task, time.time()) != "pending":
                raise HandoffError("Only a live pending handoff can notify a desktop session")
            previous = db.execute(
                "SELECT state FROM handoff_notifications WHERE lineage=? AND session_id=?", (lineage, session_id)
            ).fetchone()
            if previous:
                return {"state": previous["state"], "recovered": True}
            db.execute("INSERT INTO handoff_notifications VALUES (?, ?, 'uncertain')", (lineage, session_id))
            return {"state": "uncertain", "recovered": False, "payload": json.loads(task["payload"])}

    def finish_notification(self, lineage: str, session_id: str):
        with self._connect() as db:
            db.execute(
                "UPDATE handoff_notifications SET state='transport_written' WHERE lineage=? AND session_id=?",
                (lineage, session_id),
            )

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
        recipient = payload.get("recipient")
        if recipient is not None:
            self._identifier(recipient, "recipient")
        if len(encoded.encode("utf-8")) > 256_000:
            raise HandoffError("Handoff payload exceeds 256 KB")
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
            if recipient:
                db.execute("UPDATE handoffs SET recipient=? WHERE lineage=?", (recipient, lineage))
        return "pending"

    def claim(self, lineage: str) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
            if not row or self._state(row, time.time()) != "pending":
                raise HandoffError("Handoff is missing or already claimed; inspect it before proceeding")
            if row["recipient"] is not None:
                raise HandoffError("Targeted handoffs must be claimed with next_task and their consumer_id")
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
        if len(encoded.encode("utf-8")) > 256_000:
            raise HandoffError("Handoff result exceeds 256 KB")
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
                "SELECT task_id, state, result, updated, lease_until, consumer_id, request_id, recipient "
                "FROM handoffs WHERE lineage=?",
                (lineage,),
            ).fetchone()
            notifications = db.execute(
                "SELECT session_id, state FROM handoff_notifications WHERE lineage=? ORDER BY session_id", (lineage,)
            ).fetchall()
        if row is None:
            return None
        return {
            **dict(row),
            "state": self._state(row, time.time()),
            "result": json.loads(row["result"]) if row["result"] else None,
            "notifications": [dict(notification) for notification in notifications],
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

    @staticmethod
    def _identifier(value: str, name: str):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
            raise HandoffError(f"{name} must be a 1-128 character identifier")

    @staticmethod
    def _owned(row, handle: str):
        if (
            not row
            or not row["handle"]
            or not isinstance(handle, str)
            or not secrets.compare_digest(row["handle"].encode(), handle.encode())
        ):
            raise HandoffError("Invalid handoff handle")

    def claim_next(self, consumer_id: str, request_id: str) -> dict | None:
        """Claim atomically; the same request recovers its receipt, never a different task."""
        self._identifier(consumer_id, "consumer_id")
        self._identifier(request_id, "request_id")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM handoffs WHERE consumer_id=? AND request_id=?", (consumer_id, request_id)
            ).fetchone()
            if row:
                state = self._state(row, time.time())
                result = {"state": state, "lineage": row["lineage"], "task_id": row["task_id"], "recovered": True}
                if state == "claimed":
                    result.update(handle=row["handle"], payload=json.loads(row["payload"]))
                return result
            # Expiry does not prove an old execution stopped. Require host inspection/cancellation.
            if db.execute("SELECT 1 FROM handoffs WHERE consumer_id=? AND state='claimed'", (consumer_id,)).fetchone():
                raise HandoffError("Consumer already owns a task; inspect or finish it before requesting another")
            row = db.execute(
                "SELECT * FROM handoffs WHERE state='pending' AND lease_until>? "
                "AND (recipient IS NULL OR recipient=?) ORDER BY updated, lineage LIMIT 1",
                (time.time(), consumer_id),
            ).fetchone()
            if row is None:
                return None
            handle = secrets.token_urlsafe(32)
            db.execute(
                "UPDATE handoffs SET state='claimed', handle=?, consumer_id=?, request_id=?, updated=? WHERE lineage=?",
                (handle, consumer_id, request_id, time.time(), row["lineage"]),
            )
            return {
                "state": "claimed",
                "lineage": row["lineage"],
                "task_id": row["task_id"],
                "handle": handle,
                "payload": json.loads(row["payload"]),
                "recovered": False,
            }

    def check_consumer(self, lineage: str, handle: str, consumer_id: str):
        self._identifier(consumer_id, "consumer_id")
        with self._connect() as db:
            row = db.execute("SELECT handle, consumer_id FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
        self._owned(row, handle)
        if row["consumer_id"] != consumer_id:
            raise HandoffError("Consumer does not own this task; use submit_result for a legacy manual claim")

    def begin_wait(self, consumer_id: str, request_id: str, seconds: float):
        self._identifier(consumer_id, "consumer_id")
        self._identifier(request_id, "request_id")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM handoff_listeners WHERE waiting_until<=? AND consumer_id<>?",
                (time.time(), consumer_id),
            )
            row = db.execute("SELECT * FROM handoff_listeners WHERE consumer_id=?", (consumer_id,)).fetchone()
            if row and row["waiting_until"] > time.time() and row["request_id"] != request_id:
                raise HandoffError("Consumer already has a waiting request; recover it with the same request_id")
            if not row and db.execute("SELECT count(*) FROM handoff_listeners").fetchone()[0] >= 256:
                raise HandoffError("Too many consumer identities; reuse an existing consumer_id")
            db.execute(
                "INSERT INTO handoff_listeners VALUES (?, ?, ?) ON CONFLICT(consumer_id) DO UPDATE "
                "SET request_id=excluded.request_id, waiting_until=excluded.waiting_until",
                (consumer_id, request_id, time.time() + seconds + 2),
            )

    def end_wait(self, consumer_id: str, request_id: str):
        with self._connect() as db:
            db.execute(
                "UPDATE handoff_listeners SET waiting_until=0 WHERE consumer_id=? AND request_id=?",
                (consumer_id, request_id),
            )

    def listeners(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT consumer_id, waiting_until FROM handoff_listeners WHERE waiting_until>? ORDER BY consumer_id",
                (time.time(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def post_message(
        self, role: str, lineage: str, message_id: str, text: str, kind: str = "note", handle: str | None = None
    ) -> dict:
        self._identifier(message_id, "message_id")
        if role not in ("host", "desktop") or kind not in ("note", "directive", "progress"):
            raise HandoffError("Invalid message role or kind")
        if (kind == "directive" and role != "host") or (kind == "progress" and role != "desktop"):
            raise HandoffError("This role cannot send that message kind")
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 8192:
            raise HandoffError("Message must contain 1-8192 UTF-8 bytes")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT * FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
            if not task:
                raise HandoffError("Unknown handoff")
            if role == "desktop":
                self._owned(task, handle)
            previous = db.execute(
                "SELECT * FROM handoff_messages WHERE lineage=? AND sender=? AND message_id=?",
                (lineage, role, message_id),
            ).fetchone()
            if previous:
                if previous["text"] != text or previous["kind"] != kind:
                    raise HandoffError("Message ID already exists with different content")
                return dict(previous)
            state = self._state(task, time.time())
            if state not in (("pending", "claimed") if role == "host" else ("claimed",)):
                raise HandoffError("Handoff is not accepting new messages")
            if db.execute("SELECT count(*) FROM handoff_messages WHERE lineage=?", (lineage,)).fetchone()[0] >= 200:
                raise HandoffError("Handoff message limit reached")
            cursor = db.execute(
                "INSERT INTO handoff_messages (lineage,sender,message_id,kind,text,created) VALUES (?,?,?,?,?,?)",
                (lineage, role, message_id, kind, text, time.time()),
            )
            return dict(db.execute("SELECT * FROM handoff_messages WHERE id=?", (cursor.lastrowid,)).fetchone())

    def read_messages(
        self, role: str, lineage: str, after: int = 0, limit: int = 50, handle: str | None = None
    ) -> dict:
        if role not in ("host", "desktop") or type(after) is not int or not 0 <= after < 2**63:
            raise HandoffError("Invalid message role or cursor")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HandoffError("Message limit must be between 1 and 100")
        with self._connect() as db:
            db.execute("BEGIN")
            task = db.execute("SELECT * FROM handoffs WHERE lineage=?", (lineage,)).fetchone()
            if not task:
                raise HandoffError("Unknown handoff")
            if role == "desktop":
                self._owned(task, handle)
            rows = db.execute(
                "SELECT * FROM handoff_messages WHERE lineage=? AND sender<>? AND id>? ORDER BY id LIMIT ?",
                (lineage, role, after, limit + 1),
            ).fetchall()
            messages = [dict(row) for row in rows[:limit]]
            result = {
                "state": self._state(task, time.time()),
                "messages": messages,
                "next_after": messages[-1]["id"] if messages else after,
                "has_more": len(rows) > limit,
            }
            if role == "host" and task["result"]:
                result["result"] = json.loads(task["result"])
            return result
