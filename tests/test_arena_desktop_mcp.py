"""Exercise desktop handoff against the real Arena API, store, and Git workspace."""

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from arena.api import create_app
from arena.desktop_mcp import DesktopBridge
from arena.worker import API, WorkerError


@pytest.fixture
def desktop(tmp_path):
    app = create_app(tmp_path / "server")
    owner = (tmp_path / "server" / "owner-token.txt").read_text()
    member = TestClient(app, headers={"Authorization": "Bearer " + owner})
    invite = member.post("/api/invites", json={"name": "WorkBuddy GUI", "kind": "machine"}).json()
    project = member.post("/api/projects", json={"name": "Competition", "requirements": ["hello.txt exists"]}).json()

    class ASGIAPI(API):
        def __init__(self, server, token, *args):
            self.token = token
            self.client = TestClient(app, headers={"Authorization": "Bearer " + token, "X-Arena-Worker-Protocol": "2"})

    config = {
        "server": "http://localhost:8765",
        "workspace_root": str(tmp_path / "desktop-workspaces"),
        "name": "WorkBuddy GUI",
        "slots": 1,
        "storage_limit_bytes": 5_000_000_000,
        "agents": [
            {
                "key": "gui",
                "name": "WorkBuddy",
                "provider": "desktop_mcp",
                "roles": ["worker"],
                "capabilities": ["code", "docs"],
                "mode": "new",
                "enabled": True,
            }
        ],
    }
    with patch("arena.worker.API", ASGIAPI), patch("arena.desktop_mcp.API", ASGIAPI):
        bridge = DesktopBridge(config, invite["token"])
        yield bridge, member, app.state.store, project, invite["token"]
        bridge.close()


def test_desktop_claims_only_shared_and_submits_verified_workspace(desktop):
    bridge, member, store, project, token = desktop
    owner_id = member.get("/api/me").json()["id"]
    member.post(f'/api/projects/{project["id"]}/messages', json={"content": "private-canary", "channel": "personal"})
    memory = member.post(
        f'/api/projects/{project["id"]}/memories',
        json={"scope": "member", "category": "preference", "content": "personal-memory-canary"},
    )
    assert memory.status_code == 201
    with store.tx():
        private = store.create_task(
            project["id"], "Private worker", "secret private-canary", channel=f"personal:{owner_id}"
        )
    shared = member.post(
        f'/api/projects/{project["id"]}/tasks',
        json={"title": "Shared task", "prompt": "Write hello.txt", "capability": "code"},
    ).json()
    task = bridge.next_task()
    assert task["state"] == "ready", (task, store.task(shared["id"])["result"])
    assert task["task_id"] == shared["id"]
    assert "桌面客户端原有当前目录可能不同" in task["prompt"]
    assert store.task(private["id"])["status"] == "queued"
    serialized = json.dumps(task, ensure_ascii=False)
    assert "private-canary" not in serialized
    assert "personal-memory-canary" not in serialized
    assert token not in serialized and "run_token" not in serialized and "agent_token" not in serialized
    assert bridge.next_task()["state"] == "busy"
    bridge.resource(task["handle"], "fs/write_text_file", {"path": "hello.txt", "content": "verified"})
    assert bridge.resource(task["handle"], "fs/read_text_file", {"path": "hello.txt"})["content"] == "verified"
    with pytest.raises(WorkerError, match="nonce or requirement revision"):
        bridge.submit(task["handle"], "wrong", task["requirement_revision"], "Done")
    with pytest.raises(WorkerError, match="nonce or requirement revision"):
        bridge.submit(task["handle"], task["nonce"], task["requirement_revision"] + 1, "Done")
    result = bridge.submit(
        task["handle"], task["nonce"], task["requirement_revision"], "Created hello.txt; read it back"
    )
    assert result["state"] == "submitted"
    assert bridge.handoff["done"].wait(15)
    stored = store.task(shared["id"])
    assert stored["status"] == "in_review", stored
    assert len(stored["evidence"]["commit"]) == 40
    assert (Path(stored["evidence"]["workspace"]) / "hello.txt").read_text() == "verified"
    review = [
        t for t in store.all("SELECT * FROM tasks WHERE project_id=?", (project["id"],)) if t["kind"] == "reviewer"
    ]
    assert len(review) == 1 and review[0]["status"] == "queued"
    assert bridge.next_task()["state"] == "empty"  # neither private work nor independent review can route here


def test_desktop_command_requires_approval_and_does_not_inherit_credentials(desktop, monkeypatch):
    bridge, member, store, project, _ = desktop
    monkeypatch.setenv("OPENAI_API_KEY", "test-private-key")
    monkeypatch.setenv("GITHUB_TOKEN", "test-private-token")
    created = member.post(
        f'/api/projects/{project["id"]}/tasks',
        json={"title": "Run check", "prompt": "Check output", "capability": "code"},
    ).json()
    task = bridge.next_task()
    assert task["state"] == "ready", (task, store.task(created["id"])["result"])
    answer, errors = {}, []

    def run():
        try:
            answer.update(
                bridge.resource(
                    task["handle"],
                    "terminal/create",
                    {
                        "command": sys.executable,
                        "args": [
                            "-c",
                            "import os; assert not any(k in os.environ for k in ('OPENAI_API_KEY','GITHUB_TOKEN')); print('verified')",
                        ],
                    },
                )
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        permission = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with store.lock:
                permission = store.one("SELECT * FROM permissions WHERE task_id=?", (task["task_id"],))
            if permission:
                break
            time.sleep(0.02)
        assert permission and not answer
        response = member.post(
            f'/api/tasks/{task["task_id"]}/permissions/{permission["id"]}', json={"option_id": "allow_once"}
        )
        assert response.status_code == 200
        thread.join(5)
        assert not thread.is_alive() and not errors, errors
        terminal = answer["terminalId"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            output = bridge.resource(task["handle"], "terminal/output", {"terminalId": terminal})
            if "exitStatus" in output:
                break
            time.sleep(0.02)
        assert output["exitStatus"]["exitCode"] == 0 and "verified" in output["output"]
        bridge.submit(task["handle"], task["nonce"], task["requirement_revision"], "Command succeeded")
        assert bridge.handoff["done"].wait(15)
    finally:
        thread.join(5)


def test_desktop_identity_must_not_replace_normal_worker(desktop):
    bridge, member, _, _, token = desktop
    auth = {"Authorization": "Bearer " + token, "X-Arena-Worker-Protocol": "2"}
    bridge.worker.register()
    response = member.post(
        "/api/machines/register",
        headers=auth,
        json={
            "name": "Other",
            "os": "Linux",
            "slots": 1,
            "agents": [{"key": "other", "name": "Other", "provider": "codex", "roles": ["worker"]}],
        },
    )
    assert response.status_code == 409


def test_lost_gui_handoff_can_only_resume_after_member_confirms(desktop):
    bridge, member, store, project, _ = desktop
    created = member.post(
        f'/api/projects/{project["id"]}/tasks', json={"title": "Recover handoff", "prompt": "Write a file"}
    ).json()
    original = bridge.next_task()
    assert original["state"] == "ready", (original, store.task(created["id"])["result"])
    busy = bridge.next_task()
    assert busy["state"] == "busy" and busy["task_id"] == original["task_id"]
    assert "handle" not in busy and "nonce" not in busy
    resumed, errors = {}, []

    def recover():
        try:
            resumed.update(bridge.resume_task(busy["task_id"], busy["attempt"]))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=recover)
    thread.start()
    try:
        permission = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with store.lock:
                permission = store.one("SELECT * FROM permissions WHERE task_id=?", (original["task_id"],))
            if permission:
                break
            time.sleep(0.02)
        assert permission and not resumed
        assert "原会话已停止" in permission["tool"]
        response = member.post(
            f'/api/tasks/{original["task_id"]}/permissions/{permission["id"]}', json={"option_id": "allow_once"}
        )
        assert response.status_code == 200
        thread.join(5)
        assert not thread.is_alive() and not errors, errors
        assert resumed["handle"] == original["handle"] and resumed["nonce"] == original["nonce"]
        assert store.task(original["task_id"])["attempt"] == 1
        bridge.submit(resumed["handle"], resumed["nonce"], resumed["requirement_revision"], "Recovered and finished")
        assert bridge.handoff["done"].wait(15)
    finally:
        thread.join(5)


def test_desktop_does_not_repeat_a_claim_whose_response_was_lost(desktop):
    bridge, member, store, project, _ = desktop
    task = member.post(
        f'/api/projects/{project["id"]}/tasks', json={"title": "One claim", "prompt": "Write a file"}
    ).json()
    request = bridge.worker.machine.request
    attempts = []

    def lost_response(method, path, body=None):
        result = request(method, path, body)
        if path == "/api/machines/claim":
            attempts.append(result)
            raise httpx.ReadTimeout("response lost")
        return result

    with patch.object(bridge.worker.machine, "request", side_effect=lost_response):
        with pytest.raises(httpx.ReadTimeout):
            bridge.next_task()
        with pytest.raises(WorkerError, match="previous claim response was lost"):
            bridge.next_task()
    assert len(attempts) == 1
    assert store.task(task["id"])["status"] == "running"
    assert store.task(task["id"])["attempt"] == 1


def test_desktop_wait_observes_cancellation_and_rejects_further_operations(desktop):
    bridge, member, store, project, _ = desktop
    task = member.post(
        f'/api/projects/{project["id"]}/tasks', json={"title": "Cancel", "prompt": "Work in the task folder"}
    ).json()
    claimed = bridge.next_task()
    assert claimed["state"] == "ready"
    assert bridge.task_status(claimed["handle"])["state"] == "running"
    answer = {}
    waiter = threading.Thread(target=lambda: answer.update(bridge.task_status(claimed["handle"], 25)))
    waiter.start()
    try:
        assert member.post(f'/api/tasks/{task["id"]}/cancel').status_code == 200
        waiter.join(25)
        assert not waiter.is_alive()
        assert answer["state"] == "stopped"
        with pytest.raises(WorkerError, match="no longer accepts"):
            bridge.resource(claimed["handle"], "fs/write_text_file", {"path": "late.txt", "content": "late"})
        assert store.task(task["id"])["status"] == "cancelled"
    finally:
        waiter.join(25)


def test_two_desktop_machines_keep_task_handles_and_cancellation_separate(desktop, tmp_path):
    first, member, store, project, _ = desktop
    invite = member.post("/api/invites", json={"name": "Second desktop", "kind": "machine"}).json()
    config = json.loads(json.dumps(first.config))
    config.update(name="Second desktop", workspace_root=str(tmp_path / "second-workspaces"))
    second = DesktopBridge(config, invite["token"])
    try:
        for title in ("First work", "Second work"):
            assert (
                member.post(
                    f'/api/projects/{project["id"]}/tasks', json={"title": title, "prompt": "Work independently"}
                ).status_code
                == 201
            )
        a = first.next_task()
        b = second.next_task()
        assert a["state"] == b["state"] == "ready"
        assert a["task_id"] != b["task_id"] and a["workspace"] != b["workspace"]
        with pytest.raises(WorkerError, match="Unknown desktop task handle"):
            second.resource(a["handle"], "fs/read_text_file", {"path": "test.txt"})
        assert member.post(f'/api/tasks/{a["task_id"]}/cancel').status_code == 200
        assert first.task_status(a["handle"], 25)["state"] == "stopped"
        assert second.task_status(b["handle"])["state"] == "running"
        second.resource(b["handle"], "fs/write_text_file", {"path": "test.txt", "content": "independent"})
        second.submit(b["handle"], b["nonce"], b["requirement_revision"], "Finished second task")
        assert second.handoff["done"].wait(15)
        assert store.task(b["task_id"])["status"] == "in_review"
    finally:
        second.close()


def test_desktop_blender_result_reaches_task_evidence(desktop, monkeypatch):
    bridge, member, store, project, _ = desktop
    bridge.config["agents"][0]["capabilities"] = ["blender"]
    task = member.post(
        f'/api/projects/{project["id"]}/tasks',
        json={"title": "Blender scene", "prompt": "Create a scene", "capability": "blender"},
    ).json()
    calls = []

    def inspect(workspace, configured, cancel, run):
        calls.append(workspace)
        return {"state": "verified", "files": [{"name": "scene.blend", "version": "4.5", "scenes": 1, "objects": 1}]}

    monkeypatch.setattr("arena.worker.verify_exports", inspect)
    monkeypatch.setattr("arena.desktop_mcp.verify_exports", inspect)
    claim = bridge.next_task()
    assert claim["state"] == "ready", store.task(task["id"])
    assert bridge.inspect_blender_exports(claim["handle"])["state"] == "verified"
    bridge.resource(claim["handle"], "fs/write_text_file", {"path": "scene.txt", "content": "scene"})
    bridge.submit(claim["handle"], claim["nonce"], claim["requirement_revision"], "Created scene")
    assert bridge.handoff["done"].wait(15)
    stored = store.task(task["id"])
    assert calls and stored["evidence"]["blender_check"]["state"] == "verified"
    assert stored["status"] == "in_review"
