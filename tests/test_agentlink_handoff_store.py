from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_link.handoff_store import HandoffError, HandoffStore


def test_one_claim_survives_restart_and_retry_is_not_reissued(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    assert store.create("root", "task-1", {"workspace": "project"}) == "pending"

    def attempt():
        try:
            return HandoffStore(path).claim("root")
        except HandoffError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: attempt(), range(8)))
    claimed = [x for x in claims if x]
    assert len(claimed) == 1
    store = HandoffStore(path)
    assert store.inspect("root")["state"] == "claimed"
    with pytest.raises(HandoffError):
        store.create("root", "retry-task", {"workspace": "project"})
    with pytest.raises(HandoffError):
        store.claim("root")
    store.submit("root", claimed[0]["handle"], {"summary": "candidate"})
    store.submit("root", claimed[0]["handle"], {"summary": "candidate"})
    assert store.inspect("root")["result"] == {"summary": "candidate"}
    with pytest.raises(HandoffError):
        store.submit("root", claimed[0]["handle"], {"summary": "changed"})


def test_idempotent_create_and_wrong_handle(tmp_path):
    store = HandoffStore(tmp_path / "db")
    assert store.create("root", "task", {"prompt": "hello"}) == "pending"
    assert store.create("root", "task", {"prompt": "hello"}) == "pending"
    with pytest.raises(HandoffError):
        store.create("other", "task", {"prompt": "hello"})
    store.claim("root")
    with pytest.raises(HandoffError):
        store.submit("root", "wrong", {"summary": "done"})


def test_cancel_is_durable_and_does_not_reopen_task(tmp_path):
    path = tmp_path / "db"
    store = HandoffStore(path)
    store.create("root", "task", {"prompt": "hello"})
    claim = store.claim("root")
    assert store.cancel("root") == "cancelled"
    store = HandoffStore(path)
    assert store.cancel("root") == "cancelled"
    assert store.desktop_state("root", claim["handle"]) == "cancelled"
    assert store.pending() == []
    with pytest.raises(HandoffError):
        store.submit("root", claim["handle"], {"summary": "late"})
    with pytest.raises(HandoffError):
        store.claim("root")


def test_expired_leases_cannot_be_claimed_renewed_or_submitted(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("agent_link.handoff_store.time.time", lambda: clock[0])
    path = tmp_path / "db"
    store = HandoffStore(path)
    store.create("pending", "run-pending", {})
    store.create("claimed", "run-claimed", {})
    claim = store.claim("claimed")
    clock[0] = 1050.0
    assert store.renew("claimed", "run-claimed") == 1110.0
    clock[0] = 1060.0
    store = HandoffStore(path)
    assert store.inspect("pending")["state"] == "expired"
    assert store.pending() == []
    with pytest.raises(HandoffError):
        store.claim("pending")
    with pytest.raises(HandoffError):
        store.renew("pending", "run-pending")
    assert store.desktop_state("claimed", claim["handle"]) == "claimed"
    clock[0] = 1110.0
    assert store.desktop_state("claimed", claim["handle"]) == "expired"
    with pytest.raises(HandoffError):
        store.submit("claimed", claim["handle"], {"summary": "late"})
    with pytest.raises(HandoffError):
        store.renew("claimed", "run-claimed")
    with pytest.raises(HandoffError):
        store.create("claimed", "retry", {})


def test_legacy_database_pending_rows_expire_on_upgrade(tmp_path):
    import sqlite3

    path = tmp_path / "db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE handoffs (lineage TEXT PRIMARY KEY, task_id TEXT UNIQUE, payload TEXT, "
            "state TEXT, handle TEXT, result TEXT, updated REAL)"
        )
        db.execute("INSERT INTO handoffs VALUES ('root', 'task', '{}', 'pending', NULL, NULL, 1)")
    store = HandoffStore(path)
    assert store.inspect("root")["state"] == "expired"
    with pytest.raises(HandoffError):
        store.claim("root")


def test_workspace_cannot_be_reused_even_after_cancel_or_expiry(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = HandoffStore(tmp_path / "db")
    store.create("root", "task", {"workspace": str(workspace)})
    store.cancel("root")
    with pytest.raises(HandoffError, match="separate workspace"):
        store.create("new", "task-2", {"workspace": str(workspace / ".")})
    with pytest.raises(HandoffError, match="separate workspace"):
        store.create("new", "task-2", {"workspace": str(workspace / "nested")})
    with pytest.raises(HandoffError, match="separate workspace"):
        store.create("new", "task-2", {"workspace": str(tmp_path)})
    assert store.inspect("new") is None


def test_concurrent_overlapping_workspaces_only_allow_one_task(tmp_path):
    path = tmp_path / "db"
    workspace = tmp_path / "workspace"
    nested = workspace / "nested"
    nested.mkdir(parents=True)
    HandoffStore(path)

    def offer(item):
        lineage, folder = item
        try:
            HandoffStore(path).create(lineage, lineage, {"workspace": str(folder)})
            return "created"
        except HandoffError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(offer, [("one", workspace), ("two", nested)]))
    assert sorted(results) == ["blocked", "created"]
