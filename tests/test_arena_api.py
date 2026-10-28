import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest
from fastapi.testclient import TestClient

from arena.api import create_app


@pytest.fixture
def arena(tmp_path):
    app = create_app(tmp_path / "arena")
    token = (tmp_path / "arena" / "owner-token.txt").read_text(encoding="utf-8")
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, app.state.store


def project(client, name="比赛作品"):
    response = client.post("/api/projects", json={"name": name, "requirements": ["网页能够打开"]})
    assert response.status_code == 201, response.text
    return response.json()


def machine(client, roles=None, name="Mac mini"):
    response = client.post("/api/invites", json={"name": name, "kind": "machine"})
    token = response.json()["token"]
    auth = {"Authorization": f"Bearer {token}", "X-Arena-Worker-Protocol": "2"}
    response = client.post(
        "/api/machines/register",
        headers=auth,
        json={
            "name": name,
            "os": "macOS",
            "slots": 2,
            "agents": [
                {
                    "key": "test",
                    "name": name + " agent",
                    "provider": "fixture",
                    "roles": roles or ["leader", "worker"],
                    "capabilities": ["code", "planning"],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return auth, response.json()


def claim(client, auth):
    response = client.post("/api/machines/claim", headers=auth, json={})
    assert response.status_code == 200, response.text
    job = response.json()
    assert job
    return job, {"Authorization": "Bearer " + job["run_token"]}


def test_login_cookie_csrf_and_identity_separation(arena):
    client, store = arena
    original = client.headers.pop("Authorization").split(" ", 1)[1]
    assert client.get("/api/state").status_code == 401
    assert (
        client.post("/api/login", json={"token": original}, headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    response = client.post("/api/login", json={"token": original}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get("/api/me").json()["kind"] == "member"
    assert (
        client.post("/api/projects", json={"name": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    )
    machine_token = client.post("/api/invites", json={"name": "worker", "kind": "machine"}).json()["token"]
    assert client.get("/api/state", headers={"Authorization": "Bearer " + machine_token}).status_code == 401
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize(
    "repo",
    [
        "https://user:secret@github.com/a/b",
        "https://evil.test/a/b",
        "http://github.com/a/b",
        "https://github.com/a/b?token=secret",
        "--upload-pack=evil",
    ],
)
def test_repository_urls_reject_credentials_and_non_github(arena, repo):
    client, _ = arena
    response = client.post("/api/projects", json={"name": "x", "repo": repo})
    assert response.status_code == 422


def test_private_conversation_tasks_results_artifacts_stay_private(arena):
    client, store = arena
    p = project(client)
    partner = client.post("/api/invites", json={"name": "Partner", "kind": "member"}).json()
    partner_auth = {"Authorization": "Bearer " + partner["token"]}
    task = client.post(
        f'/api/projects/{p["id"]}/messages', json={"content": "个人草案：private-canary", "channel": "personal"}
    ).json()["task"]
    assert task["channel"].startswith("personal:")
    owner_state = client.get("/api/state", params={"project_id": p["id"], "channel": "personal"}).json()
    assert "private-canary" in json.dumps(owner_state, ensure_ascii=False)
    shared = client.get("/api/state", params={"project_id": p["id"]}).json()
    assert "private-canary" not in json.dumps(shared, ensure_ascii=False)
    partner_state = client.get(
        "/api/state", headers=partner_auth, params={"project_id": p["id"], "channel": "personal"}
    ).json()
    assert "private-canary" not in json.dumps(partner_state, ensure_ascii=False)
    assert client.get("/api/tasks/" + task["id"], headers=partner_auth).status_code == 404
    assert client.post("/api/tasks/" + task["id"] + "/cancel", headers=partner_auth).status_code == 404
    artifact = client.post(
        f'/api/projects/{p["id"]}/artifacts',
        params={"name": "private.txt", "task_id": task["id"]},
        content=b"private payload",
    ).json()
    assert client.get(f'/api/artifacts/{artifact["id"]}/download', headers=partner_auth).status_code == 404
    assert (
        client.post(f'/api/artifacts/{artifact["id"]}/pin', headers=partner_auth, json={"pinned": False}).status_code
        == 404
    )
    auth, _ = machine(client)
    _, run_auth = claim(client, auth)
    assert client.post("/api/runs/requirements", headers=run_auth, json={"requirements": ["secret"]}).status_code == 409
    child = client.post(
        "/api/runs/tasks", headers=run_auth, json={"title": "private child", "prompt": "Draft privately"}
    ).json()
    assert child["channel"] == task["channel"]
    assert client.post("/api/runs/complete", headers=run_auth, json={"result": "private-result"}).status_code == 200
    assert "private-result" not in json.dumps(client.get("/api/state", params={"project_id": p["id"]}).json())


def test_cancel_revokes_lease_and_offline_state_expires(arena):
    client, store = arena
    p = project(client)
    auth, _ = machine(client)
    task = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "实现", "prompt": "Write code"}).json()
    _, run_auth = claim(client, auth)
    assert client.post("/api/tasks/" + task["id"] + "/cancel").status_code == 200
    assert client.post("/api/runs/heartbeat", headers=run_auth).status_code == 410
    assert client.post("/api/runs/complete", headers=run_auth, json={"result": "late result"}).status_code == 410
    assert client.post("/api/tasks/" + task["id"] + "/retry").status_code == 200
    job, _ = claim(client, auth)
    with store.tx():
        store.db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time.time() - 1, job["task"]["id"]))
    state = client.get("/api/state", params={"project_id": p["id"]}).json()
    expired = next(t for t in state["tasks"] if t["id"] == task["id"])
    assert expired["status"] == "interrupted"
    assert "lease_hash" not in expired
    assert "agent_hash" not in expired
    assert "run_token" not in json.dumps(state)


def test_revision_compare_and_set_prevents_lost_partner_changes(arena):
    client, _ = arena
    p = project(client)
    url = f'/api/projects/{p["id"]}/requirements'
    response = client.post(url, json={"requirements": ["新版要求"], "expected_revision": 1})
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert client.post(url, json={"requirements": ["覆盖新版"], "expected_revision": 1}).status_code == 409
    assert client.post(url, json={"requirements": ["没有版本的覆盖"]}).status_code == 422
    assert client.post(url, json={"requirements": ["新版要求"]}).json()["revision"] == 2


def test_stale_leader_cannot_overwrite_partner_requirements_without_fresh_revision(arena):
    client, store = arena
    p = project(client)
    client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "负责人", "prompt": "Plan", "kind": "leader"})
    auth, _ = machine(client, roles=["leader"])
    _, leader = claim(client, auth)
    response = client.post(
        f'/api/projects/{p["id"]}/requirements', json={"requirements": ["Partner 的新要求"], "expected_revision": 1}
    )
    assert response.status_code == 200
    stale = client.post("/api/runs/requirements", headers=leader, json={"requirements": ["旧上下文得出的要求"]})
    assert stale.status_code == 409
    assert store.project(p["id"])["requirements"] == ["Partner 的新要求"]
    context = client.get("/api/runs/context", headers=leader).json()
    updated = client.post(
        "/api/runs/requirements",
        headers=leader,
        json={
            "requirements": ["Partner 的新要求", "补充可验证要求"],
            "expected_revision": context["project"]["revision"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 3


def test_permissions_validate_options_and_current_attempt(arena):
    client, _ = arena
    p = project(client)
    auth, _ = machine(client)
    task = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "执行", "prompt": "Write code"}).json()
    _, run_auth = claim(client, auth)
    permission = {
        "permissions": [
            {"id": "p1", "tool": {"title": "write file"}, "options": [{"optionId": "allow-once", "name": "允许一次"}]}
        ]
    }
    assert client.post("/api/runs/permissions", headers=run_auth, json=permission).status_code == 200
    decision_url = f'/api/tasks/{task["id"]}/permissions/p1'
    assert client.post(decision_url, json={"option_id": "allow-always"}).status_code == 409
    assert client.post(decision_url, json={"option_id": "allow-once"}).status_code == 200
    assert client.get("/api/runs/permissions", headers=run_auth).json() == {
        "decisions": [{"id": "p1", "option_id": "allow-once"}]
    }
    assert client.post(decision_url, json={"option_id": None}).status_code == 409
    client.post("/api/tasks/" + task["id"] + "/cancel")
    client.post("/api/tasks/" + task["id"] + "/retry")
    _, new_run_auth = claim(client, auth)
    assert client.get("/api/runs/permissions", headers=new_run_auth).json() == {"decisions": []}
    assert client.post(decision_url, json={"option_id": "allow-once"}).status_code == 404


def test_artifact_quota_pin_and_download_headers(arena):
    client, store = arena
    p = project(client)
    url = f'/api/projects/{p["id"]}/artifacts'
    response = client.post(url, params={"name": "../../hello.html"}, content=b"<script>bad()</script>")
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert artifact["name"] == "hello.html"
    download = client.get(f'/api/artifacts/{artifact["id"]}/download')
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert client.delete("/api/artifacts/" + artifact["id"]).status_code == 409
    assert client.post(f'/api/artifacts/{artifact["id"]}/pin', json={"pinned": False}).status_code == 200
    assert client.delete("/api/artifacts/" + artifact["id"]).status_code == 200
    store.quota = 1
    assert client.post(url, params={"name": "oversize.bin"}, content=b"xxxx").status_code == 413
    assert not list((store.root / "artifacts").iterdir())


def test_end_to_end_independent_review_gates_project_delivery(arena):
    client, _ = arena
    p = client.post(
        "/api/projects",
        json={"name": "比赛作品", "repo": "https://github.com/example/repo", "requirements": ["网页能够打开"]},
    ).json()
    worker_auth, _ = machine(client, roles=["worker"], name="Worker")
    reviewer_auth, _ = machine(client, roles=["reviewer"], name="Reviewer")
    leader_auth, _ = machine(client, roles=["leader"], name="Leader")
    work = client.post(
        f'/api/projects/{p["id"]}/tasks', json={"title": "实现网页", "prompt": "Write index.html"}
    ).json()
    _, execution = claim(client, worker_auth)
    assert (
        client.post(
            "/api/runs/review",
            headers=execution,
            json={"decision": "accept", "summary": "self-review", "evidence": ["nothing"]},
        ).status_code
        == 409
    )
    completed = client.post(
        "/api/runs/complete",
        headers=execution,
        json={"result": "网页已完成", "evidence": {"commit": "a" * 40, "delivery": "github"}},
    )
    assert completed.json()["status"] == "in_review"
    review_job, review_auth = claim(client, reviewer_auth)
    assert review_job["source"]["id"] == work["id"]
    assert (
        client.post(
            "/api/runs/review",
            headers=review_auth,
            json={"decision": "accept", "summary": "页面测试通过", "evidence": ["Opened index.html"]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/runs/complete",
            headers=review_auth,
            json={"result": "验收通过", "evidence": {"commit": "a" * 40}},
        ).status_code
        == 200
    )
    leader_job, leader_run = claim(client, leader_auth)
    assert leader_job["task"]["kind"] == "leader"
    assert (
        client.post("/api/runs/finish", headers=leader_run, json={"summary": "交付页面与验证记录"}).json()["status"]
        == "ready"
    )
    assert client.post("/api/runs/complete", headers=leader_run, json={"result": "项目交付完成"}).status_code == 200
    state = client.get("/api/state", params={"project_id": p["id"]}).json()
    assert state["projects"][0]["status"] == "ready"
    assert all(t["status"] == "done" for t in state["tasks"])


def test_requirements_change_reopens_old_work_for_revalidation(arena):
    client, store = arena
    p = project(client)
    work = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "原任务", "prompt": "Implement"}).json()
    with store.tx():
        store.db.execute(
            'UPDATE tasks SET status="done",review=? WHERE id=?', (json.dumps({"decision": "accept"}), work["id"])
        )
    response = client.post(
        f'/api/projects/{p["id"]}/requirements', json={"requirements": ["新增移动端验收"], "expected_revision": 1}
    )
    assert response.status_code == 200
    task = client.get("/api/tasks/" + work["id"]).json()
    assert task["status"] == "changes_requested"
    retry = client.post("/api/tasks/" + work["id"] + "/retry")
    assert retry.status_code == 200
    assert retry.json()["revision"] == 2
    assert retry.json()["acceptance"] == ["新增移动端验收"]
    assert retry.json()["review"] is None


def test_cancelled_scope_does_not_prevent_delivery_of_accepted_replacement(arena):
    client, store = arena
    p = project(client)
    old = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "旧方案", "prompt": "Old scope"}).json()
    assert client.post("/api/tasks/" + old["id"] + "/cancel").status_code == 200
    work = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "替代方案", "prompt": "New scope"}).json()
    with store.tx():
        store.db.execute(
            'UPDATE tasks SET status="done",review=? WHERE id=?', (json.dumps({"decision": "accept"}), work["id"])
        )
    client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "汇总", "prompt": "Summarize", "kind": "leader"})
    auth, _ = machine(client, roles=["leader"])
    _, run_auth = claim(client, auth)
    response = client.post("/api/runs/finish", headers=run_auth, json={"summary": "按替代方案交付"})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("policy", [{"auto_merge": True}, {"auto_deploy": True}])
def test_release_policy_requires_explicit_local_profile(arena, policy):
    client, _ = arena
    assert client.post("/api/projects", json={"name": "x", "policy": policy}).status_code == 422


def test_release_is_deduplicated_and_requirements_cannot_change_mid_publish(arena):
    client, store = arena
    response = client.post(
        "/api/projects",
        json={
            "name": "可发布作品",
            "repo": "https://github.com/example/repo",
            "requirements": ["验收通过"],
            "policy": {"auto_merge": True, "push_branches": True, "release_profile": "competition"},
        },
    )
    assert response.status_code == 201, response.text
    p = response.json()
    work = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "网页", "prompt": "Implement"}).json()
    with store.tx():
        store.db.execute(
            'UPDATE tasks SET status="done",review=?,evidence=? WHERE id=?',
            (
                json.dumps({"decision": "accept", "evidence": ["tests pass"]}),
                json.dumps({"commit": "a" * 40, "delivery": "github"}),
                work["id"],
            ),
        )
    client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "汇总", "prompt": "Summarize", "kind": "leader"})
    auth, _ = machine(client, roles=["leader"])
    _, run_auth = claim(client, auth)
    first = client.post("/api/runs/finish", headers=run_auth, json={"summary": "发布验收过的提交"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "publishing"
    second = client.post("/api/runs/finish", headers=run_auth, json={"summary": "重复确认"})
    assert second.status_code == 200 and second.json()["task_id"] == first.json()["task_id"]
    publisher = store.task(first.json()["task_id"])
    assert publisher["kind"] == "publisher"
    assert publisher["capability"] == "release:competition"
    assert publisher["dependencies"] == [work["id"]]
    with store.tx():
        store.db.execute('UPDATE tasks SET status="running" WHERE id=?', (publisher["id"],))
    changed = client.post(
        f'/api/projects/{p["id"]}/requirements', json={"requirements": ["变更要求"], "expected_revision": 1}
    )
    assert changed.status_code == 409
    client.post("/api/tasks/" + publisher["id"] + "/cancel")
    assert (
        client.post(
            f'/api/projects/{p["id"]}/requirements', json={"requirements": ["变更要求"], "expected_revision": 1}
        ).status_code
        == 200
    )


def test_never_dispatched_release_can_be_replaced_after_scope_change(arena):
    client, store = arena
    p = client.post(
        "/api/projects",
        json={
            "name": "发布更新",
            "repo": "https://github.com/example/repo",
            "requirements": ["检查作品"],
            "policy": {"auto_merge": True, "push_branches": True, "release_profile": "competition"},
        },
    ).json()
    worker = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "作品", "prompt": "Implement"}).json()
    with store.tx():
        store.db.execute(
            'UPDATE tasks SET status="done",review=?,evidence=? WHERE id=?',
            (
                json.dumps({"decision": "accept", "evidence": ["tests pass"]}),
                json.dumps({"commit": "a" * 40, "delivery": "github"}),
                worker["id"],
            ),
        )
    client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "交付", "prompt": "Deliver", "kind": "leader"})
    auth, _ = machine(client, roles=["leader"])
    _, run_auth = claim(client, auth)
    old = client.post("/api/runs/finish", headers=run_auth, json={"summary": "第一次确认"}).json()["task_id"]
    with store.tx():
        store.db.execute('UPDATE tasks SET status="changes_requested" WHERE id=?', (old,))
    response = client.post("/api/runs/finish", headers=run_auth, json={"summary": "范围更新后重新确认"})
    assert response.status_code == 200, response.text
    assert response.json()["task_id"] != old
    assert store.task(old)["status"] == "cancelled"
    assert store.task(response.json()["task_id"])["dependencies"] == [worker["id"]]
    with store.tx():
        store.db.execute('UPDATE tasks SET status="failed",attempt=1 WHERE id=?', (response.json()["task_id"],))
    assert client.post("/api/runs/finish", headers=run_auth, json={"summary": "不能重复发布"}).status_code == 409


@pytest.mark.parametrize("attempt,expected_state", [(0, "active"), (1, "blocked")])
def test_cancelled_publisher_releases_project_publishing_state(arena, attempt, expected_state):
    client, store = arena
    p = project(client)
    with store.tx():
        task = store.create_task(p["id"], "发布", "Release", kind="publisher", capability="release:test")
        store.db.execute("UPDATE tasks SET attempt=? WHERE id=?", (attempt, task["id"]))
        store.db.execute('UPDATE projects SET status="publishing" WHERE id=?', (p["id"],))
    response = client.post("/api/tasks/" + task["id"] + "/cancel")
    assert response.status_code == 200
    assert store.project(p["id"])["status"] == expected_state


def test_shared_retry_cannot_change_scope_while_publisher_runs(arena):
    client, store = arena
    p = project(client)
    work = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "已取消工作", "prompt": "Old scope"}).json()
    assert client.post("/api/tasks/" + work["id"] + "/cancel").status_code == 200
    with store.tx():
        publishing = store.create_task(p["id"], "发布", "Release", kind="publisher", capability="release:test")
        store.db.execute('UPDATE tasks SET status="running" WHERE id=?', (publishing["id"],))
    assert client.post("/api/tasks/" + work["id"] + "/retry").status_code == 409
    assert store.task(work["id"])["status"] == "cancelled"


def test_restart_preserves_login_running_lease_and_dispatch_receipt(tmp_path):
    root = tmp_path / "persistent-arena"
    first_app = create_app(root)
    owner_token = (root / "owner-token.txt").read_text(encoding="utf-8")
    with TestClient(first_app) as first:
        first.headers["Authorization"] = "Bearer " + owner_token
        p = project(first)
        machine_auth, _ = machine(first, roles=["leader"])
        payload = {"content": "研究比赛需求", "client_request_id": "persist-request-1"}
        sent = first.post(f'/api/projects/{p["id"]}/messages', json=payload)
        assert sent.status_code == 201
        _, run_auth = claim(first, machine_auth)
        first.headers.pop("Authorization")
        assert first.post("/api/login", json={"token": owner_token}).status_code == 200
        cookies = dict(first.cookies)
    second_app = create_app(root)
    with TestClient(second_app) as second:
        second.cookies.update(cookies)
        assert second.get("/api/me").status_code == 200
        assert second.post("/api/runs/heartbeat", headers=run_auth).status_code == 200
        repeated = second.post(f'/api/projects/{p["id"]}/messages', json=payload)
        assert repeated.status_code == 201
        assert repeated.json()["id"] == sent.json()["id"]
        state = second.get("/api/state", params={"project_id": p["id"]}).json()
        assert len(state["tasks"]) == 1 and len(state["messages"]) == 1
        conflicting = second.post(f'/api/projects/{p["id"]}/messages', json={**payload, "content": "different request"})
        assert conflicting.status_code == 409
        assert second.post("/api/logout").status_code == 200
        assert second.get("/api/me").status_code == 401
    third_app = create_app(root)
    with TestClient(third_app) as third:
        third.cookies.update(cookies)
        assert third.get("/api/me").status_code == 401


def test_credential_rotation_revocation_and_last_member_protection(arena):
    client, _ = arena
    old_owner = client.headers["Authorization"]
    owner = client.get("/api/me").json()
    assert client.delete("/api/identities/" + owner["id"]).status_code == 409
    partner = client.post("/api/invites", json={"name": "Partner", "kind": "member"}).json()
    assert client.post("/api/identities/" + partner["id"] + "/rotate").status_code == 403
    partner_client = TestClient(client.app)
    assert partner_client.post("/api/login", json={"token": partner["token"]}).status_code == 200
    rotated = client.post("/api/identities/" + owner["id"] + "/rotate")
    assert rotated.status_code == 200
    assert client.get("/api/me", headers={"Authorization": old_owner}).status_code == 401
    client.headers.pop("Authorization")
    assert client.get("/api/me").status_code == 200
    assert client.delete("/api/identities/" + partner["id"]).status_code == 200
    assert partner_client.get("/api/me").status_code == 401
    assert partner_client.post("/api/login", json={"token": partner["token"]}).status_code == 401
    identities = client.get("/api/identities").json()["identities"]
    assert next(item for item in identities if item["id"] == partner["id"])["revoked"] is True
    assert "token_hash" not in json.dumps(identities)
    assert client.delete("/api/identities/" + owner["id"]).status_code == 409


def test_revoking_machine_invalidates_current_run_and_registration(arena):
    client, store = arena
    p = project(client)
    machine_auth, machine_info = machine(client)
    work = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "任务", "prompt": "Work"}).json()
    _, run_auth = claim(client, machine_auth)
    assert client.delete("/api/identities/" + machine_info["machine_id"]).status_code == 200
    assert client.post("/api/machines/heartbeat", headers=machine_auth, json={}).status_code == 401
    expired = client.post("/api/runs/heartbeat", headers=run_auth)
    assert expired.status_code == 410
    assert expired.json()["detail"]["code"] == "run_inactive"
    assert store.task(work["id"])["status"] == "interrupted"
    assert not store.snapshot()["agents"][0]["enabled"]


def test_project_settings_cas_and_execution_scope_updates(arena):
    client, store = arena
    p = project(client)
    guard = {"expected_revision": p["revision"], "expected_settings_version": p["settings_version"]}
    renamed = client.patch("/api/projects/" + p["id"], json={**guard, "name": "新名字"})
    assert renamed.status_code == 200, renamed.text
    p2 = renamed.json()
    assert p2["revision"] == p["revision"] and p2["settings_version"] != p["settings_version"]
    assert client.patch("/api/projects/" + p["id"], json={**guard, "name": "partner overwrite"}).status_code == 409
    assert client.patch("/api/projects/" + p["id"], json={**guard, "name": "新名字"}).status_code == 200
    updated = client.patch(
        "/api/projects/" + p["id"],
        json={
            "expected_revision": p2["revision"],
            "expected_settings_version": p2["settings_version"],
            "policy": {"routing": "economy", "preferred_providers": ["qoder"]},
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["policy"]["routing"] == "economy"
    p3 = updated.json()
    task = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "任务", "prompt": "Work"}).json()
    change = {
        "expected_revision": p3["revision"],
        "expected_settings_version": p3["settings_version"],
        "repo": "https://github.com/example/repo",
    }
    assert client.patch("/api/projects/" + p["id"], json=change).status_code == 409
    change.pop("repo")
    change["brief"] = "原需求加入新约束"
    revised = client.patch("/api/projects/" + p["id"], json=change)
    assert revised.status_code == 200 and revised.json()["revision"] == p3["revision"] + 1
    assert store.task(task["id"])["status"] == "changes_requested"


def test_memory_approval_snapshot_refresh_and_personal_isolation(arena):
    client, _ = arena
    p = project(client)
    public = client.post(
        f'/api/projects/{p["id"]}/memories', json={"scope": "project", "category": "decision", "content": "使用 Python"}
    ).json()
    personal = client.post(
        f'/api/projects/{p["id"]}/memories',
        json={"scope": "member", "category": "preference", "content": "私有偏好 owner-only"},
    ).json()
    partner = client.post("/api/invites", json={"name": "Partner", "kind": "member"}).json()
    partner_auth = {"Authorization": "Bearer " + partner["token"]}
    assert (
        client.get(f'/api/projects/{p["id"]}/memories', headers=partner_auth, params={"scope": "member"}).json()[
            "memories"
        ]
        == []
    )
    assert (
        client.put(
            "/api/memories/" + personal["id"], headers=partner_auth, json={"status": "active", "expected_revision": 1}
        ).status_code
        == 404
    )
    client.post(f'/api/projects/{p["id"]}/messages', json={"content": "分析需求"})
    machine_auth, _ = machine(client, roles=["leader"])
    _, run_auth = claim(client, machine_auth)
    snapshot = client.get("/api/runs/context", headers=run_auth).json()["memories"]
    assert [memory["id"] for memory in snapshot] == [public["id"]]
    proposed = client.post(
        "/api/runs/memories", headers=run_auth, json={"category": "decision", "content": "先做可访问性检查"}
    )
    assert proposed.status_code == 201 and proposed.json()["status"] == "proposed"
    suggestion = proposed.json()
    assert (
        client.post(
            "/api/runs/memories", headers=run_auth, json={"content": "自动写入", "status": "active"}
        ).status_code
        == 422
    )
    approved = client.put("/api/memories/" + suggestion["id"], json={"status": "active", "expected_revision": 1})
    assert approved.status_code == 200 and approved.json()["revision"] == 2
    unchanged = client.get("/api/runs/memories", headers=run_auth).json()["memories"]
    assert suggestion["id"] not in {memory["id"] for memory in unchanged}
    refreshed = client.get("/api/runs/memories", headers=run_auth, params={"refresh": True}).json()["memories"]
    assert suggestion["id"] in {memory["id"] for memory in refreshed}
    assert (
        client.put(
            "/api/memories/" + suggestion["id"], json={"content": "stale overwrite", "expected_revision": 1}
        ).status_code
        == 409
    )
    disabled = client.put("/api/memories/" + suggestion["id"], json={"status": "disabled", "expected_revision": 2})
    assert disabled.status_code == 200
    assert client.post("/api/runs/complete", headers=run_auth, json={"result": "规划完成"}).status_code == 200
    client.post(f'/api/projects/{p["id"]}/messages', json={"content": "个人想法", "channel": "personal"})
    _, private_run = claim(client, machine_auth)
    private_snapshot = client.get("/api/runs/context", headers=private_run).json()["memories"]
    assert {memory["id"] for memory in private_snapshot} == {public["id"], personal["id"]}


def test_cancelling_parent_revokes_running_descendant_leases(arena):
    client, store = arena
    p = project(client)
    parent = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "父任务", "prompt": "Coordinate"}).json()
    child = client.post(
        f'/api/projects/{p["id"]}/tasks', json={"title": "子任务", "prompt": "Work", "parent_id": parent["id"]}
    ).json()
    first_machine, _ = machine(client, roles=["worker"], name="A")
    second_machine, _ = machine(client, roles=["worker"], name="B")
    first_job = client.post("/api/machines/claim", headers=first_machine).json()
    if not first_job:
        first_job = client.post("/api/machines/claim", headers=second_machine).json()
    second_job = client.post("/api/machines/claim", headers=second_machine).json()
    if not second_job:
        second_job = client.post("/api/machines/claim", headers=first_machine).json()
    assert first_job and second_job
    assert client.post("/api/tasks/" + parent["id"] + "/cancel").status_code == 200
    assert store.task(child["id"])["status"] == "cancelled"
    for job in (first_job, second_job):
        assert (
            client.post("/api/runs/heartbeat", headers={"Authorization": "Bearer " + job["run_token"]}).status_code
            == 410
        )


def test_retry_preserves_task_acceptance_for_same_requirement_version(arena):
    client, store = arena
    p = project(client)
    task = client.post(
        f'/api/projects/{p["id"]}/tasks',
        json={
            "title": "前端子任务",
            "prompt": "Create navigation",
            "acceptance": ["导航键盘操作通过"],
        },
    ).json()
    with store.tx():
        store.db.execute('UPDATE tasks SET status="failed" WHERE id=?', (task["id"],))
        store.db.execute('UPDATE projects SET status="ready" WHERE id=?', (p["id"],))
    retried = client.post("/api/tasks/" + task["id"] + "/retry")
    assert retried.status_code == 200
    assert retried.json()["acceptance"] == ["导航键盘操作通过"]
    assert store.project(p["id"])["status"] == "active"


def test_automatic_release_rejects_local_and_unconfirmed_inputs_before_dispatch(arena):
    client, store = arena
    body = {
        "name": "实际发布",
        "repo": "https://github.com/example/repo",
        "requirements": ["已独立测试"],
        "policy": {"auto_merge": True, "release_profile": "competition"},
    }
    refused = client.post("/api/projects", json=body)
    assert refused.status_code == 422
    assert "push_branches" in refused.text
    body["policy"]["push_branches"] = True
    p = client.post("/api/projects", json=body).json()
    work = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "成果", "prompt": "Implement"}).json()
    with store.tx():
        store.db.execute(
            'UPDATE tasks SET status="done",review=? WHERE id=?',
            (json.dumps({"decision": "accept", "evidence": ["tested exact commit"]}), work["id"]),
        )
    client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "交付", "prompt": "Finish", "kind": "leader"})
    auth, _ = machine(client, roles=["leader"])
    _, leader = claim(client, auth)
    for evidence in (
        {"commit": "a" * 40, "delivery": "local"},
        {"commit": "a" * 40, "delivery": "github", "push_status": "unconfirmed"},
    ):
        with store.tx():
            store.db.execute("UPDATE tasks SET evidence=? WHERE id=?", (json.dumps(evidence), work["id"]))
        response = client.post("/api/runs/finish", headers=leader, json={"summary": "尝试发布"})
        assert response.status_code == 409, response.text
        assert "GitHub" in response.text
        assert not store.one('SELECT id FROM tasks WHERE project_id=? AND kind="publisher"', (p["id"],))
    with store.tx():
        store.db.execute(
            "UPDATE tasks SET evidence=? WHERE id=?",
            (json.dumps({"commit": "a" * 40, "delivery": "github"}), work["id"]),
        )
    assert client.post("/api/runs/finish", headers=leader, json={"summary": "同步确认后发布"}).status_code == 200


def test_leader_recovers_known_failure_with_bounded_attempts(arena):
    client, store = arena
    p = project(client)
    worker_auth, worker_registration = machine(client, roles=["worker"], name="Worker")
    leader_auth, _ = machine(client, roles=["leader"], name="Leader")
    worker_id = next(iter(worker_registration["agents"].values()))
    task = client.post(
        f'/api/projects/{p["id"]}/tasks',
        json={"title": "修复编译", "prompt": "Build", "acceptance": ["build passes"], "requested_agent": worker_id},
    ).json()
    _, execution = claim(client, worker_auth)
    assert (
        client.post(
            "/api/runs/complete", headers=execution, json={"result": "Known compile error", "failed": True}
        ).status_code
        == 200
    )
    job, _ = claim(client, leader_auth)
    assert job["task"]["title"] == "处理执行阻塞"
    leader = {"Authorization": "Bearer " + job["agent_token"]}
    route = f'/api/runs/tasks/{task["id"]}/recover'
    for attempt in (2, 3):
        retried = client.post(route, headers=leader, json={"action": "retry", "requested_agent": None})
        assert retried.status_code == 200, retried.text
        assert retried.json()["requested_agent"] is None
        assert retried.json()["acceptance"] == ["build passes"]
        assert client.post(route, headers=leader, json={"action": "retry"}).status_code == 409
        work, execution = claim(client, worker_auth)
        assert work["task"]["attempt"] == attempt
        assert (
            client.post(
                "/api/runs/complete", headers=execution, json={"result": "Known compiler failure", "failed": True}
            ).status_code
            == 200
        )
    assert client.post(route, headers=leader, json={"action": "retry"}).status_code == 409
    assert store.task(task["id"])["status"] == "failed"
    assert client.post(route, headers=leader, json={"action": "cancel"}).status_code == 200
    assert store.task(task["id"])["status"] == "cancelled"


def test_leader_recovery_cannot_mutate_unknown_outcomes_or_other_channels(arena):
    client, store = arena
    p = project(client)
    client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "协调", "prompt": "Coordinate", "kind": "leader"})
    leader_auth, _ = machine(client, roles=["leader"])
    job, _ = claim(client, leader_auth)
    leader = {"Authorization": "Bearer " + job["agent_token"]}
    task = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "执行", "prompt": "Work"}).json()
    route = f'/api/runs/tasks/{task["id"]}/recover'
    for status, evidence in (
        ("interrupted", {}),
        ("failed", {"execution_uncertain": True}),
        ("failed", {"outcome_unknown": True}),
        ("failed", {"push_status": "unconfirmed"}),
    ):
        with store.tx():
            store.db.execute(
                "UPDATE tasks SET status=?,attempt=1,evidence=? WHERE id=?", (status, json.dumps(evidence), task["id"])
            )
        for action in ("retry", "cancel"):
            assert client.post(route, headers=leader, json={"action": action}).status_code == 409
        assert store.task(task["id"])["status"] == status
    owner = client.get("/api/me").json()["id"]
    other = project(client, "另一个项目")
    with store.tx():
        private = store.create_task(p["id"], "个人", "Private", channel=f"personal:{owner}")
        foreign = store.create_task(other["id"], "别的项目", "Other")
        publisher = store.create_task(p["id"], "发布", "Release", kind="publisher", capability="release:test")
        for target in (private, foreign, publisher):
            store.db.execute('UPDATE tasks SET status="failed",attempt=1 WHERE id=?', (target["id"],))
    for target, expected in ((private, 404), (foreign, 404), (publisher, 409)):
        assert (
            client.post(f'/api/runs/tasks/{target["id"]}/recover', headers=leader, json={"action": "retry"}).status_code
            == expected
        )
        assert store.task(target["id"])["status"] == "failed"


def test_leader_cancellation_does_not_stop_running_descendants(arena):
    client, store = arena
    p = project(client)
    client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "协调", "prompt": "Coordinate", "kind": "leader"})
    leader_auth, _ = machine(client, roles=["leader"], name="Leader")
    _, leader = claim(client, leader_auth)
    parent = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "父任务", "prompt": "Work"}).json()
    child = client.post(
        f'/api/projects/{p["id"]}/tasks', json={"title": "子任务", "prompt": "Work", "parent_id": parent["id"]}
    ).json()
    with store.tx():
        store.db.execute('UPDATE tasks SET status="failed",attempt=1 WHERE id=?', (parent["id"],))
    worker_auth, _ = machine(client, roles=["worker"], name="Worker")
    _, child_run = claim(client, worker_auth)
    response = client.post(f'/api/runs/tasks/{parent["id"]}/recover', headers=leader, json={"action": "cancel"})
    assert response.status_code == 409
    assert store.task(parent["id"])["status"] == "failed"
    assert store.task(child["id"])["status"] == "running"
    assert client.post("/api/runs/heartbeat", headers=child_run).status_code == 200


def test_reviewer_retry_requires_source_still_waiting_for_review(arena):
    client, store = arena
    p = project(client)
    with store.tx():
        source = store.create_task(p["id"], "源任务", "Work")
        review = store.create_task(p["id"], "验收", "Review", kind="reviewer", source_id=source["id"])
        store.db.execute('UPDATE tasks SET status="failed",attempt=1 WHERE id=?', (review["id"],))
    assert client.post(f'/api/tasks/{review["id"]}/retry').status_code == 409
    with store.tx():
        store.db.execute('UPDATE tasks SET status="in_review" WHERE id=?', (source["id"],))
    assert client.post(f'/api/tasks/{review["id"]}/retry').status_code == 200


def test_artifact_retention_counts_only_requested_channel(arena):
    client, store = arena
    p = project(client)
    owner_id = client.get("/api/me").json()["id"]
    partner = client.post("/api/invites", json={"name": "Partner", "kind": "member"}).json()
    partner_auth = {"Authorization": "Bearer " + partner["token"]}
    with store.tx():
        personal = store.create_task(p["id"], "个人成果", "Private", channel=f"personal:{owner_id}")
        other = store.create_task(p["id"], "搭档成果", "Private", channel=f'personal:{partner["id"]}')
        for task in (personal, other):
            store.db.execute('UPDATE tasks SET status="done" WHERE id=?', (task["id"],))
    artifacts = []
    for name, task_id, auth in (
        ("shared-old.txt", None, None),
        ("shared-current.txt", None, None),
        ("personal.txt", personal["id"], None),
        ("partner.txt", other["id"], partner_auth),
    ):
        params = {"name": name}
        if task_id:
            params["task_id"] = task_id
        uploaded = client.post(f'/api/projects/{p["id"]}/artifacts', params=params, headers=auth, content=name.encode())
        assert uploaded.status_code == 201, uploaded.text
        artifact = uploaded.json()
        assert (
            client.post(f'/api/artifacts/{artifact["id"]}/pin', headers=auth, json={"pinned": False}).status_code == 200
        )
        artifacts.append(artifact)
    response = client.post("/api/artifacts/cleanup", json={"project_id": p["id"], "keep": 1})
    assert response.json()["deleted"] == [artifacts[0]["id"]]
    assert all(store.one("SELECT id FROM artifacts WHERE id=?", (a["id"],)) for a in artifacts[1:])
    response = client.post("/api/artifacts/cleanup", json={"project_id": p["id"], "keep": 0, "channel": "personal"})
    assert response.json()["deleted"] == [artifacts[2]["id"]]
    assert store.one("SELECT id FROM artifacts WHERE id=?", (artifacts[1]["id"],))
    assert store.one("SELECT id FROM artifacts WHERE id=?", (artifacts[3]["id"],))


def test_workspace_status_is_machine_owned_and_exact_attempt_only(arena):
    client, store = arena
    p = project(client)
    first_auth, registration = machine(client, roles=["worker"], name="A")
    second_auth, _ = machine(client, roles=["worker"], name="B")
    task = client.post(
        f'/api/projects/{p["id"]}/tasks',
        json={"title": "工作目录", "prompt": "Build", "requested_agent": next(iter(registration["agents"].values()))},
    ).json()
    _, execution = claim(client, first_auth)
    evidence = {
        "commit": "a" * 40,
        "delivery": "github",
        "workspace": "retained",
        "machine_id": registration["machine_id"],
    }
    assert (
        client.post("/api/runs/complete", headers=execution, json={"result": "Done", "evidence": evidence}).status_code
        == 200
    )
    route = "/api/machines/workspaces/status"
    body = {"tasks": [{"id": task["id"], "attempt": 1}, {"id": "unknown", "attempt": 1}]}
    response = client.post(route, headers=first_auth, json=body)
    assert response.status_code == 200
    assert response.json()["tasks"] == [
        {
            "id": task["id"],
            "attempt": 1,
            "project_id": p["id"],
            "status": "in_review",
            "kind": "worker",
            "revision": 1,
            "evidence": evidence,
        }
    ]
    assert client.post(route, headers=second_auth, json=body).json()["tasks"] == []
    assert client.post(route, json=body).status_code == 401
    with store.tx():
        store.db.execute("UPDATE tasks SET attempt=2 WHERE id=?", (task["id"],))
    assert client.post(route, headers=first_auth, json=body).json()["tasks"] == []


def test_revocation_during_login_cannot_resurrect_a_member_session(arena, monkeypatch):
    client, store = arena
    partner = client.post("/api/invites", json={"name": "Partner", "kind": "member"}).json()
    login_client = TestClient(client.app)
    authenticated = threading.Event()
    continue_login = threading.Event()
    original = store.authenticate

    def pause_login(token, cookie=False):
        actor = original(token, cookie=cookie)
        if token == partner["token"] and not cookie:
            authenticated.set()
            assert continue_login.wait(3)
        return actor

    monkeypatch.setattr(store, "authenticate", pause_login)
    with ThreadPoolExecutor(max_workers=2) as pool:
        login = pool.submit(login_client.post, "/api/login", json={"token": partner["token"]})
        assert authenticated.wait(3)
        revoked = pool.submit(client.delete, f'/api/identities/{partner["id"]}')
        try:
            revoked.result(timeout=0.2)
        except TimeoutError:
            pass
        finally:
            continue_login.set()
        assert login.result(timeout=3).status_code in (200, 401)
        assert revoked.result(timeout=3).status_code == 200
    assert login_client.get("/api/me").status_code == 401
    assert not store.one("SELECT token_hash FROM sessions WHERE identity_id=?", (partner["id"],))


def test_legacy_worker_cannot_register_or_claim_completion_credentials(arena):
    client, store = arena
    p = project(client)
    auth, _ = machine(client, roles=["worker"])
    task = client.post(f'/api/projects/{p["id"]}/tasks', json={"title": "待执行", "prompt": "Work"}).json()
    legacy = {"Authorization": auth["Authorization"]}
    registration = client.post("/api/machines/register", headers=legacy, json={"name": "Old", "os": "macOS"})
    assert registration.status_code == 409
    assert registration.json()["detail"]["code"] == "worker_upgrade_required"
    denied = client.post("/api/machines/claim", headers=legacy)
    assert denied.status_code == 409
    assert store.task(task["id"])["status"] == "queued"
    assert client.post("/api/machines/claim", headers=auth).json()["agent_token"]
