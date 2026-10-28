"""Upgrade real legacy SQLite layouts without constructing them through the new Store."""

import json
import sqlite3
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from arena.api import create_app
from arena.store import Store, digest

LEGACY_SCHEMA = """
CREATE TABLE identities (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
  token_hash TEXT UNIQUE NOT NULL, created REAL NOT NULL);
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY, identity_id TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, brief TEXT NOT NULL, repo TEXT NOT NULL,
  base_ref TEXT NOT NULL, requirements TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 1,
  policy TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL);
CREATE TABLE machines (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, os TEXT NOT NULL, seen REAL NOT NULL,
  slots INTEGER NOT NULL, storage_bytes INTEGER NOT NULL DEFAULT 0);
CREATE TABLE agents (
  id TEXT PRIMARY KEY, machine_id TEXT NOT NULL, name TEXT NOT NULL, provider TEXT NOT NULL,
  roles TEXT NOT NULL, capabilities TEXT NOT NULL, model TEXT, preference INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, mode TEXT NOT NULL DEFAULT 'new');
CREATE TABLE messages (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, author TEXT NOT NULL, content TEXT NOT NULL,
  channel TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_id TEXT, source_id TEXT,
  title TEXT NOT NULL, prompt TEXT NOT NULL, kind TEXT NOT NULL, capability TEXT NOT NULL,
  acceptance TEXT NOT NULL, dependencies TEXT NOT NULL, status TEXT NOT NULL,
  agent_id TEXT, requested_agent TEXT, revision INTEGER NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0, lease_hash TEXT, lease_until REAL,
  result TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '{}',
  review TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, task_id TEXT,
  kind TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE artifacts (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, name TEXT NOT NULL,
  size INTEGER NOT NULL, pinned INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE permissions (
  task_id TEXT NOT NULL, attempt INTEGER NOT NULL, id TEXT NOT NULL,
  tool TEXT NOT NULL, options TEXT NOT NULL, decided INTEGER NOT NULL DEFAULT 0,
  option_id TEXT, PRIMARY KEY(task_id,attempt,id));
"""


@pytest.fixture(params=[False, True], ids=["before-task-channels", "before-agent-tokens-and-routing"])
def legacy_data(tmp_path, request):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "artifacts").mkdir()
    owner_token = "legacy-owner-token-that-must-remain-valid"
    machine_token = "legacy-machine-token-that-must-remain-valid"
    run_token = "legacy-run-token-previously-given-to-agent"
    (root / "owner-token.txt").write_text(owner_token, encoding="utf-8")
    project_id, artifact_id = "1" * 32, "2" * 32
    member_id, machine_id = "3" * 32, "4" * 32
    done_id, running_id = "5" * 32, "6" * 32
    agent_id = uuid.uuid5(uuid.NAMESPACE_URL, machine_id + ":worker").hex
    artifact = b"preserved legacy artifact\x00"
    (root / "artifacts" / artifact_id).write_bytes(artifact)
    evidence = {
        "commit": "a" * 40,
        "delivery": "github",
        "machine_id": machine_id,
        "workspace": "/legacy/workspace",
        "artifacts": [{"id": artifact_id, "name": "legacy.blend", "size": len(artifact)}],
    }
    now = time.time()
    database = sqlite3.connect(root / "arena.db")
    try:
        database.executescript(LEGACY_SCHEMA)
        if request.param:
            database.execute("ALTER TABLE tasks ADD COLUMN channel TEXT NOT NULL DEFAULT 'shared'")
        database.executemany(
            "INSERT INTO identities VALUES(?,?,?,?,?)",
            [
                (member_id, "Existing owner", "member", digest(owner_token), now),
                (machine_id, "Existing machine", "machine", digest(machine_token), now),
            ],
        )
        database.execute(
            "INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                "Existing competition",
                "Keep the previous work",
                "https://github.com/fixture/legacy",
                "main",
                json.dumps(["Keep delivered files"]),
                4,
                json.dumps({"push_branches": True}),
                "active",
                now,
            ),
        )
        database.execute("INSERT INTO machines VALUES(?,?,?,?,?,?)", (machine_id, "Existing Mac", "macOS", now, 2, 123))
        database.execute(
            "INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?,?)",
            (agent_id, machine_id, "Legacy worker", "fixture", '["worker"]', '["code"]', None, 10, 1, "new"),
        )
        for task_id, status in ((done_id, "done"), (running_id, "running")):
            database.execute(
                "INSERT INTO tasks(id,project_id,title,prompt,kind,capability,acceptance,dependencies,status,"
                "agent_id,revision,attempt,lease_hash,lease_until,result,evidence,review,created,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    project_id,
                    "Legacy " + status,
                    "Preserve this work",
                    "worker",
                    "code",
                    '["Keep delivered files"]',
                    "[]",
                    status,
                    agent_id,
                    4,
                    2,
                    digest(run_token) if status == "running" else None,
                    now + 3600 if status == "running" else None,
                    "Previously recorded result",
                    json.dumps(evidence),
                    (
                        json.dumps({"decision": "accept", "summary": "Previously reviewed", "evidence": ["Old checks"]})
                        if status == "done"
                        else None
                    ),
                    now,
                    now,
                ),
            )
        database.execute(
            "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)",
            (artifact_id, project_id, done_id, "legacy.blend", len(artifact), 1, now),
        )
        database.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?)",
            ("7" * 32, project_id, "Existing owner", "The original conversation", "shared", now),
        )
        database.commit()
        assert "agent_hash" not in {row[1] for row in database.execute("PRAGMA table_info(tasks)")}
        assert not database.execute("SELECT name FROM sqlite_master WHERE name='memories'").fetchone()
    finally:
        database.close()
    return {
        "root": root,
        "owner_token": owner_token,
        "machine_token": machine_token,
        "run_token": run_token,
        "member_id": member_id,
        "machine_id": machine_id,
        "agent_id": agent_id,
        "project_id": project_id,
        "done_id": done_id,
        "running_id": running_id,
        "artifact_id": artifact_id,
        "artifact": artifact,
        "evidence": evidence,
    }


def test_store_upgrade_preserves_records_and_applies_defaults_once(legacy_data):
    old = legacy_data
    store = Store(old["root"])
    try:
        assert store.authenticate(old["owner_token"])["id"] == old["member_id"]
        assert store.authenticate(old["machine_token"])["id"] == old["machine_id"]
        assert store.project(old["project_id"])["revision"] == 4
        assert store.project(old["project_id"])["policy"] == {"push_branches": True}
        task = store.task(old["done_id"])
        assert task["status"] == "done" and task["attempt"] == 2
        assert task["evidence"] == old["evidence"]
        assert task["review"]["decision"] == "accept"
        assert task["channel"] == "shared"
        assert task["difficulty"] == "standard" and task["context_tokens"] == 0
        assert task["memory_snapshot"] == [] and task["routing_reason"] == ""
        assert task["agent_hash"] is None and task["recovery_notified"] == 0
        agent = store.one("SELECT * FROM agents WHERE id=?", (old["agent_id"],))
        assert json.loads(agent["readiness"]) == {} and json.loads(agent["routing_profile"]) == {}
        assert store.active_memories(old["project_id"]) == []
        schema_version = store.db.execute("PRAGMA schema_version").fetchone()[0]
        with store.tx():
            store.db.execute("UPDATE tasks SET difficulty='deep',context_tokens=8192 WHERE id=?", (old["done_id"],))
        events = store.all("SELECT * FROM events ORDER BY id")
    finally:
        store.db.close()
    again = Store(old["root"])
    try:
        assert again.db.execute("PRAGMA schema_version").fetchone()[0] == schema_version
        assert again.one("SELECT count(*) AS n FROM identities")["n"] == 2
        assert again.one("SELECT count(*) AS n FROM tasks")["n"] == 2
        assert again.task(old["done_id"])["difficulty"] == "deep"
        assert again.task(old["done_id"])["context_tokens"] == 8192
        assert again.task(old["done_id"])["evidence"] == old["evidence"]
        assert again.all("SELECT * FROM events ORDER BY id") == events
        assert (old["root"] / "owner-token.txt").read_text(encoding="utf-8") == old["owner_token"]
        assert (old["root"] / "artifacts" / old["artifact_id"]).read_bytes() == old["artifact"]
    finally:
        again.db.close()


def test_legacy_api_login_records_and_new_work_survive_upgrade(legacy_data):
    old = legacy_data
    app = create_app(old["root"])
    with TestClient(app) as client:
        login = client.post("/api/login", json={"token": old["owner_token"]})
        assert login.status_code == 200, login.text
        assert client.get("/api/me").json()["id"] == old["member_id"]
        state = client.get("/api/state", params={"project_id": old["project_id"]})
        assert state.status_code == 200, state.text
        assert state.json()["projects"][0]["name"] == "Existing competition"
        assert any(message["content"] == "The original conversation" for message in state.json()["messages"])
        download = client.get(f'/api/artifacts/{old["artifact_id"]}/download')
        assert download.status_code == 200 and download.content == old["artifact"]
        machine_auth = {"Authorization": "Bearer " + old["machine_token"], "X-Arena-Worker-Protocol": "2"}
        registered = client.post(
            "/api/machines/register",
            headers=machine_auth,
            json={
                "name": "Upgraded Mac",
                "os": "macOS",
                "slots": 2,
                "agents": [
                    {
                        "key": "worker",
                        "name": "Legacy worker",
                        "provider": "fixture",
                        "roles": ["worker"],
                        "capabilities": ["code"],
                    }
                ],
            },
        )
        assert registered.status_code == 200, registered.text
        task = client.post(
            f'/api/projects/{old["project_id"]}/tasks', json={"title": "New work", "prompt": "Continue after upgrade"}
        )
        assert task.status_code == 201, task.text
        claimed = client.post("/api/machines/claim", headers=machine_auth, json={})
        assert claimed.status_code == 200, claimed.text
        job = claimed.json()
        assert job is not None and job["task"]["id"] == task.json()["id"]
        assert job["run_token"] != job["agent_token"]
        context = client.get("/api/runs/context", headers={"Authorization": "Bearer " + job["agent_token"]})
        assert context.status_code == 200 and context.json()["memories"] == []


def test_upgrade_interrupts_legacy_runs_whose_full_worker_token_was_given_to_the_agent(legacy_data):
    old = legacy_data
    app = create_app(old["root"])
    with TestClient(app) as client:
        task = app.state.store.task(old["running_id"])
        assert task["status"] == "interrupted"
        assert task["lease_hash"] is None and task["lease_until"] is None and task["agent_hash"] is None
        assert task["evidence"] == old["evidence"] and task["attempt"] == 2
        assert app.state.store.one("SELECT seen FROM machines WHERE id=?", (old["machine_id"],))["seen"] == 0
        notices = app.state.store.all("SELECT * FROM events WHERE task_id=?", (old["running_id"],))
        assert any(notice["kind"] == "interrupted" and notice["text"] for notice in notices)
        run_auth = {"Authorization": "Bearer " + old["run_token"]}
        assert client.get("/api/runs/context", headers=run_auth).status_code == 410
        assert client.post("/api/runs/heartbeat", headers=run_auth, json={}).status_code == 410
        assert (
            client.post("/api/runs/complete", headers=run_auth, json={"result": "Late legacy completion"}).status_code
            == 410
        )


def test_old_worker_cannot_rejoin_without_the_current_token_protocol(legacy_data):
    old = legacy_data
    app = create_app(old["root"])
    old_machine_auth = {"Authorization": "Bearer " + old["machine_token"]}
    with TestClient(app) as client:
        registered = client.post(
            "/api/machines/register",
            headers=old_machine_auth,
            json={
                "name": "Old worker",
                "os": "macOS",
                "slots": 1,
                "agents": [
                    {
                        "key": "worker",
                        "name": "Legacy worker",
                        "provider": "fixture",
                        "roles": ["worker"],
                        "capabilities": ["code"],
                    }
                ],
            },
        )
        claimed = client.post("/api/machines/claim", headers=old_machine_auth, json={})
        heartbeat = client.post("/api/machines/heartbeat", headers=old_machine_auth, json={"storage_bytes": 0})
        for response in (registered, claimed, heartbeat):
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "worker_upgrade_required"
        assert app.state.store.one("SELECT seen FROM machines WHERE id=?", (old["machine_id"],))["seen"] == 0
