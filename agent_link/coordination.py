"""Single-authority reservation service; Multica remains the task scheduler."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:-]+$")]


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: Identifier
    lineage: Identifier
    task_id: Identifier
    instance_id: Identifier


class InspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: Identifier
    lineage: Identifier


class ReservationStore:
    """Durable claims never expire automatically; uncertainty blocks re-execution."""

    def __init__(self, path: Path):
        self.path = str(path)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS reservations (workspace TEXT NOT NULL, lineage TEXT NOT NULL, "
                "task_id TEXT NOT NULL, machine TEXT NOT NULL, instance TEXT NOT NULL, created REAL NOT NULL, "
                "PRIMARY KEY(workspace, lineage), UNIQUE(workspace, task_id))"
            )

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, request: ReservationRequest, machine: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM reservations WHERE workspace=? AND (lineage=? OR task_id=?)",
                (request.workspace_id, request.lineage, request.task_id),
            ).fetchall()
            if rows:
                if len(rows) == 1 and all(
                    rows[0][key] == value
                    for key, value in (
                        ("lineage", request.lineage),
                        ("task_id", request.task_id),
                        ("machine", machine),
                        ("instance", request.instance_id),
                    )
                ):
                    return {"status": "reserved", "created_at": rows[0]["created"]}
                raise ValueError("This task or lineage is already reserved; inspect before retrying")
            now = time.time()
            db.execute(
                "INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?)",
                (request.workspace_id, request.lineage, request.task_id, machine, request.instance_id, now),
            )
        return {"status": "reserved", "created_at": now}

    def inspect(self, workspace_id: str, lineage: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT task_id, machine, instance, created FROM reservations WHERE workspace=? AND lineage=?",
                (workspace_id, lineage),
            ).fetchone()
        return dict(row) if row else None

    def owned(self, request: ReservationRequest, machine: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT task_id, machine, instance FROM reservations WHERE workspace=? AND lineage=?",
                (request.workspace_id, request.lineage),
            ).fetchone()
        return bool(
            row
            and row["task_id"] == request.task_id
            and row["machine"] == machine
            and row["instance"] == request.instance_id
        )


def build_app(store: ReservationStore, machine_tokens: dict) -> FastAPI:
    """Each machine has a unique random token and explicit workspace access."""
    tokens = []
    for machine, config in machine_tokens.items():
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", machine) or not isinstance(config, dict):
            raise ValueError("Invalid machine configuration")
        token, workspaces = config.get("token"), config.get("workspaces")
        if not isinstance(token, str) or len(token) < 32 or not isinstance(workspaces, list) or not workspaces:
            raise ValueError("Each machine requires a strong token and explicit workspaces")
        if any(not isinstance(w, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", w) for w in workspaces):
            raise ValueError("Invalid workspace ID")
        digest = hashlib.sha256(token.encode()).digest()
        if any(hmac.compare_digest(digest, existing[0]) for existing in tokens):
            raise ValueError("Machine tokens must be distinct")
        tokens.append((digest, machine, set(workspaces)))
    if not tokens:
        raise ValueError("At least one machine is required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def authenticate(authorization: str, workspace: str) -> str:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid machine token")
        digest = hashlib.sha256(authorization[7:].encode()).digest()
        match = next((entry for entry in tokens if hmac.compare_digest(entry[0], digest)), None)
        if match is None:
            raise HTTPException(status_code=401, detail="Invalid machine token")
        if workspace not in match[2]:
            raise HTTPException(status_code=403, detail="Workspace not authorized")
        return match[1]

    @app.post("/v1/reservations")
    def reserve(request: ReservationRequest, authorization: str = Header(default="")) -> dict:
        machine = authenticate(authorization, request.workspace_id)
        try:
            return store.reserve(request, machine)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.post("/v1/reservations/inspect")
    def inspect(request: InspectRequest, authorization: str = Header(default="")) -> dict:
        authenticate(authorization, request.workspace_id)
        row = store.inspect(request.workspace_id, request.lineage)
        return {"status": "reserved", **row} if row else {"status": "missing"}

    @app.post("/v1/reservations/check")
    def check(request: ReservationRequest, authorization: str = Header(default="")) -> dict:
        machine = authenticate(authorization, request.workspace_id)
        if not store.owned(request, machine):
            raise HTTPException(status_code=409, detail="Reservation is not owned by this task and installation")
        return {"status": "reserved"}

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Local reservation service behind a private HTTPS proxy")
    parser.add_argument("--db", required=True, help="Absolute path to the coordinator SQLite database")
    parser.add_argument("--config", required=True, help="Absolute path to private machine token configuration")
    parser.add_argument("--port", type=int, default=8781)
    args = parser.parse_args()
    db, config = Path(args.db).expanduser(), Path(args.config).expanduser()
    if not db.is_absolute() or not db.parent.is_dir() or not config.is_absolute() or not config.is_file():
        parser.error("--db and --config require absolute paths in existing directories")
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    with config.open(encoding="utf-8") as file:
        machine_tokens = json.load(file)
    if not isinstance(machine_tokens, dict):
        parser.error("configuration must be a JSON object")
    uvicorn.run(build_app(ReservationStore(db), machine_tokens), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
