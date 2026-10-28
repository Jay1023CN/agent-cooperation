"""Cross-module delivery sequences using isolated API clients and local Git only."""

import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from arena.api import create_app
from arena.worker import prepare_workspace, task_prompt


@pytest.fixture
def arena(tmp_path):
    app = create_app(tmp_path / "arena")
    owner = (tmp_path / "arena" / "owner-token.txt").read_text(encoding="utf-8")
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {owner}"
        response = client.post(
            "/api/projects",
            json={
                "name": "Competition",
                "requirements": ["Works offline"],
                "repo": "https://github.com/example/competition",
            },
        )
        assert response.status_code == 201
        yield client, app.state.store, response.json()


def post(client, path, payload=None, auth=None):
    response = client.post(path, json=payload or {}, headers=auth or {})
    assert response.status_code < 300, response.text
    return response.json()


def register(client, role, name=None):
    name = name or role
    identity = post(client, "/api/invites", {"name": name, "kind": "machine"})
    auth = {"Authorization": "Bearer " + identity["token"], "X-Arena-Worker-Protocol": "2"}
    registration = {
        "name": name,
        "os": "fixture",
        "slots": 2,
        "agents": [
            {
                "key": "stable-instance",
                "name": name,
                "provider": "fixture",
                "roles": [role],
                "capabilities": ["code", "planning"],
            }
        ],
    }
    result = post(client, "/api/machines/register", registration, auth)
    return auth, registration, result


def take(client, machine_auth):
    job = post(client, "/api/machines/claim", auth=machine_auth)
    assert job is not None
    return job, {"Authorization": "Bearer " + job["run_token"]}


def create_work(client, project, title, **fields):
    return post(client, f'/api/projects/{project["id"]}/tasks', {"title": title, "prompt": "Implement", **fields})


def finish_work(client, auth):
    return post(
        client,
        "/api/runs/complete",
        {"result": "Implemented and checked", "evidence": {"commit": "a" * 40, "delivery": "github"}},
        auth,
    )


def accept(client, reviewer_auth):
    job, run = take(client, reviewer_auth)
    post(
        client,
        "/api/runs/review",
        {"decision": "accept", "summary": "Checked actual work", "evidence": ["Automated checks passed"]},
        run,
    )
    post(client, "/api/runs/complete", {"result": "Accepted", "evidence": job["source"]["evidence"]}, run)
    return job


def test_repeated_leader_requirement_update_preserves_accepted_work(arena):
    client, store, project = arena
    worker, _, _ = register(client, "worker")
    reviewer, _, _ = register(client, "reviewer")
    leader, _, _ = register(client, "leader")
    work = create_work(client, project, "Feature")
    _, run = take(client, worker)
    finish_work(client, run)
    accept(client, reviewer)
    _, supervisor = take(client, leader)
    for _ in range(2):
        returned = post(
            client,
            "/api/runs/requirements",
            {"requirements": project["requirements"], "expected_revision": 1},
            supervisor,
        )
        assert returned["revision"] == 1
    assert store.task(work["id"])["status"] == "done"
    assert store.task(work["id"])["review"]["decision"] == "accept"
    assert post(client, "/api/runs/finish", {"summary": "Ready"}, supervisor)["status"] == "ready"


def test_queued_leader_adopts_current_spec_without_incrementing_it(arena):
    client, store, project = arena
    leader, _, _ = register(client, "leader")
    response = post(client, f'/api/projects/{project["id"]}/messages', {"content": "Plan the work"})
    task_id = response["task"]["id"]
    post(
        client,
        f'/api/projects/{project["id"]}/requirements',
        {"requirements": ["Works offline", "Supports mobile"], "expected_revision": 1},
    )
    job, run = take(client, leader)
    assert job["project"]["revision"] == 2
    response = post(client, "/api/runs/requirements", {"requirements": job["project"]["requirements"]}, run)
    assert response["revision"] == 2
    result = post(client, "/api/runs/complete", {"result": "Planned against current requirements"}, run)
    assert result["status"] == "done"
    assert store.task(task_id)["revision"] == 2


def test_followup_leader_prompt_contains_latest_partner_chat(arena):
    client, _, project = arena
    leader, _, _ = register(client, "leader")
    partner = post(client, "/api/invites", {"name": "Partner", "kind": "member"})
    partner_auth = {"Authorization": "Bearer " + partner["token"]}
    post(client, f'/api/projects/{project["id"]}/messages', {"content": "First request"})
    _, first_run = take(client, leader)
    post(client, f'/api/projects/{project["id"]}/messages', {"content": "fresh-context-marker"}, partner_auth)
    assert post(client, "/api/machines/claim", auth=leader) is None
    post(client, "/api/runs/complete", {"result": "First response"}, first_run)
    followup, next_run = take(client, leader)
    context = client.get("/api/runs/context", headers=next_run)
    assert context.status_code == 200
    followup["context"] = context.json()
    prompt = task_prompt(followup)
    assert "fresh-context-marker" in prompt
    assert "First response" in prompt
    assert "Partner" in prompt


def test_cancelled_obsolete_work_does_not_suppress_automatic_delivery_summary(arena):
    client, _, project = arena
    worker, _, _ = register(client, "worker")
    reviewer, _, _ = register(client, "reviewer")
    leader, _, _ = register(client, "leader")
    obsolete = create_work(client, project, "Obsolete feature")
    post(client, f'/api/tasks/{obsolete["id"]}/cancel')
    create_work(client, project, "Retained feature")
    _, run = take(client, worker)
    finish_work(client, run)
    accept(client, reviewer)
    summary = post(client, "/api/machines/claim", auth=leader)
    assert summary is not None
    assert summary["task"]["kind"] == "leader"


def test_cancelling_shared_dependency_marks_private_consumer_blocked(arena):
    client, store, project = arena
    shared = create_work(client, project, "Shared prerequisite")
    leader, _, _ = register(client, "leader")
    post(client, f'/api/projects/{project["id"]}/messages', {"content": "Use the shared module", "channel": "personal"})
    _, private_run = take(client, leader)
    consumer = post(
        client,
        "/api/runs/tasks",
        {"title": "Private experiment", "prompt": "Use shared module", "dependencies": [shared["id"]]},
        private_run,
    )
    post(client, f'/api/tasks/{shared["id"]}/cancel')
    private = store.task(consumer["id"])
    assert private["status"] == "blocked"
    assert shared["id"] in private["result"]


def test_cancelled_child_does_not_strand_parent_coordination(arena):
    client, store, project = arena
    worker, _, _ = register(client, "worker")
    reviewer, _, _ = register(client, "reviewer")
    parent = create_work(client, project, "Integrate retained work")
    _, parent_run = take(client, worker)
    obsolete = post(client, "/api/runs/tasks", {"title": "Unused child", "prompt": "Old approach"}, parent_run)
    retained = post(client, "/api/runs/tasks", {"title": "Needed child", "prompt": "Current approach"}, parent_run)
    assert finish_work(client, parent_run)["status"] == "coordinating"
    post(client, f'/api/tasks/{obsolete["id"]}/cancel')
    child_job, child_run = take(client, worker)
    assert child_job["task"]["id"] == retained["id"]
    finish_work(client, child_run)
    accept(client, reviewer)
    assert store.task(parent["id"])["status"] == "queued"
    resumed, _ = take(client, worker)
    assert resumed["task"]["id"] == parent["id"]
    assert [t["id"] for t in resumed["dependencies"]] == [retained["id"]]


def test_reviewer_failure_recovers_without_reexecuting_source(arena):
    client, store, project = arena
    worker, _, _ = register(client, "worker")
    reviewer, _, _ = register(client, "reviewer")
    original = create_work(client, project, "Feature")
    _, execution = take(client, worker)
    finish_work(client, execution)
    verification, review_run = take(client, reviewer)
    post(client, "/api/runs/complete", {"result": "Reviewer process exited", "failed": True}, review_run)
    assert store.task(original["id"])["status"] == "in_review"
    post(client, f'/api/tasks/{verification["task"]["id"]}/retry')
    recovered = accept(client, reviewer)
    assert recovered["task"]["id"] == verification["task"]["id"]
    assert recovered["task"]["attempt"] == 2
    assert store.task(original["id"])["attempt"] == 1
    assert store.task(original["id"])["status"] == "done"


def test_machine_restart_preserves_identity_and_does_not_duplicate_unknown_work(arena):
    client, store, project = arena
    machine_auth, payload, first_registration = register(client, "worker")
    original = create_work(client, project, "Running work")
    first, old_run = take(client, machine_auth)
    payload["name"] = "Renamed Mac mini"
    payload["agents"][0]["name"] = "Renamed agent"
    second_registration = post(client, "/api/machines/register", payload, machine_auth)
    assert first_registration["machine_id"] == second_registration["machine_id"]
    assert set(first_registration["agents"].values()) == set(second_registration["agents"].values())
    assert post(client, "/api/machines/claim", auth=machine_auth) is None
    with store.tx():
        store.db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time.time() - 1, original["id"]))
    assert post(client, "/api/machines/claim", auth=machine_auth) is None
    assert store.task(original["id"])["status"] == "interrupted"
    assert client.post("/api/runs/complete", headers=old_run, json={"result": "late"}).status_code in (409, 410)
    post(client, f'/api/tasks/{original["id"]}/retry')
    recovered, _ = take(client, machine_auth)
    assert recovered["task"]["id"] == first["task"]["id"]
    assert recovered["task"]["attempt"] == 2


def test_duplicate_display_names_keep_private_conversations_separate(arena):
    client, _, project = arena
    first = post(client, "/api/invites", {"name": "Partner", "kind": "member"})
    second = post(client, "/api/invites", {"name": "Partner", "kind": "member"})
    first_auth = {"Authorization": "Bearer " + first["token"]}
    second_auth = {"Authorization": "Bearer " + second["token"]}
    post(
        client,
        f'/api/projects/{project["id"]}/messages',
        {"content": "name-is-not-identity-secret", "channel": "personal"},
        first_auth,
    )
    state = client.get(
        "/api/state", params={"project_id": project["id"], "channel": "personal"}, headers=second_auth
    ).json()
    assert "name-is-not-identity-secret" not in json.dumps(state)


def test_new_project_can_start_from_empty_git_repository(tmp_path, monkeypatch):
    """Use a local bare remote, never GitHub credentials or network calls."""
    git_config = tmp_path / "empty-git-config"
    git_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    remote = tmp_path / "empty.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    monkeypatch.setattr("arena.worker.checked_repo", lambda _: str(remote))
    root = tmp_path / "workspaces"
    root.mkdir()
    job = {
        "task": {"id": "first-task", "attempt": 1, "kind": "worker", "evidence": {}},
        "project": {"id": "new-project", "repo": "https://github.com/example/empty", "base_ref": "main"},
        "source": None,
        "dependencies": [],
    }
    workspace, branch = prepare_workspace(job, root, "machine")
    assert workspace.is_dir()
    assert branch == "arena/first-task/1"
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True).returncode == 0


def test_cancelling_last_pending_child_wakes_already_waiting_parent(arena):
    client, store, project = arena
    worker, _, _ = register(client, "worker")
    reviewer, _, _ = register(client, "reviewer")
    parent = create_work(client, project, "Parent")
    _, parent_run = take(client, worker)
    retained = post(client, "/api/runs/tasks", {"title": "Needed child", "prompt": "Retained work"}, parent_run)
    obsolete = post(client, "/api/runs/tasks", {"title": "Unused child", "prompt": "Obsolete work"}, parent_run)
    finish_work(client, parent_run)
    child, child_run = take(client, worker)
    assert child["task"]["id"] == retained["id"]
    finish_work(client, child_run)
    accept(client, reviewer)
    assert store.task(parent["id"])["status"] == "coordinating"
    post(client, f'/api/tasks/{obsolete["id"]}/cancel')
    assert store.task(parent["id"])["status"] == "queued"
    resumed, _ = take(client, worker)
    assert resumed["task"]["id"] == parent["id"]


def test_cancelling_last_obsolete_task_wakes_delivery_summary(arena):
    client, _, project = arena
    worker, _, _ = register(client, "worker")
    reviewer, _, _ = register(client, "reviewer")
    leader, _, _ = register(client, "leader")
    create_work(client, project, "Retained work")
    obsolete = create_work(client, project, "Obsolete work")
    _, run = take(client, worker)
    finish_work(client, run)
    accept(client, reviewer)
    assert post(client, "/api/machines/claim", auth=leader) is None
    post(client, f'/api/tasks/{obsolete["id"]}/cancel')
    summary = post(client, "/api/machines/claim", auth=leader)
    assert summary is not None
    assert summary["task"]["kind"] == "leader"


def test_cancelling_delivery_summary_does_not_recreate_it(arena):
    client, store, project = arena
    worker, _, _ = register(client, "worker")
    reviewer, _, _ = register(client, "reviewer")
    leader, _, _ = register(client, "leader")
    create_work(client, project, "Retained work")
    _, execution = take(client, worker)
    finish_work(client, execution)
    accept(client, reviewer)
    summaries = [t for t in store.snapshot(project["id"])["tasks"] if t["kind"] == "leader"]
    assert len(summaries) == 1
    post(client, f'/api/tasks/{summaries[0]["id"]}/cancel')
    assert post(client, "/api/machines/claim", auth=leader) is None
    assert store.task(summaries[0]["id"])["status"] == "cancelled"


@pytest.mark.parametrize("notification_outcome", ["complete", "cancel"])
def test_failure_recovery_notification_is_deduplicated_until_a_new_failure(arena, notification_outcome):
    client, store, project = arena
    worker, _, _ = register(client, "worker")
    leader, _, _ = register(client, "leader")
    work = create_work(client, project, "Failing work")
    _, execution = take(client, worker)
    post(client, "/api/runs/complete", {"result": "Known fixture failure", "failed": True}, execution)
    notices = [t for t in store.snapshot(project["id"])["tasks"] if t["kind"] == "leader"]
    assert len(notices) == 1
    assert work["id"] in notices[0]["prompt"]
    if notification_outcome == "complete":
        notice, supervisor = take(client, leader)
        assert notice["task"]["id"] == notices[0]["id"]
        post(client, "/api/runs/complete", {"result": "Explained failure to member"}, supervisor)
    else:
        post(client, f'/api/tasks/{notices[0]["id"]}/cancel')
    with store.tx():
        for _ in range(3):
            store.reconcile(project["id"])
    assert post(client, "/api/machines/claim", auth=leader) is None
    assert store.task(work["id"])["status"] == "failed"
    assert store.task(work["id"])["attempt"] == 1
    assert len([t for t in store.snapshot(project["id"])["tasks"] if t["kind"] == "leader"]) == 1
    post(client, f'/api/tasks/{work["id"]}/retry')
    _, retried = take(client, worker)
    post(client, "/api/runs/complete", {"result": "New failure after explicit retry", "failed": True}, retried)
    next_notice, _ = take(client, leader)
    assert next_notice["task"]["id"] != notices[0]["id"]
    assert "New failure after explicit retry" in next_notice["task"]["prompt"]
    assert store.task(work["id"])["attempt"] == 2


def test_interrupted_worker_notifies_leader_without_duplicating_unknown_work(arena):
    client, store, project = arena
    worker, _, _ = register(client, "worker")
    leader, _, _ = register(client, "leader")
    work = create_work(client, project, "Lost connection")
    take(client, worker)
    with store.tx():
        store.db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time.time() - 1, work["id"]))
    response = client.get("/api/state", params={"project_id": project["id"]})
    assert response.status_code == 200
    assert store.task(work["id"])["status"] == "interrupted"
    assert store.task(work["id"])["attempt"] == 1
    assert post(client, "/api/machines/claim", auth=worker) is None
    notice, _ = take(client, leader)
    assert work["id"] in notice["task"]["prompt"]
    assert "结果未知" in notice["task"]["prompt"]
    assert len([t for t in store.snapshot(project["id"])["tasks"] if t["kind"] == "worker"]) == 1


def test_private_failure_recovery_details_stay_in_owners_conversation(arena):
    client, store, project = arena
    leader, _, _ = register(client, "leader")
    worker, _, _ = register(client, "worker")
    post(client, f'/api/projects/{project["id"]}/messages', {"content": "Private experiment", "channel": "personal"})
    _, supervisor = take(client, leader)
    private = post(client, "/api/runs/tasks", {"title": "Private work", "prompt": "Experiment"}, supervisor)
    post(client, "/api/runs/complete", {"result": "Work assigned"}, supervisor)
    work, execution = take(client, worker)
    assert work["task"]["id"] == private["id"]
    post(client, "/api/runs/complete", {"result": "PRIVATE_FAILURE_DETAIL", "failed": True}, execution)
    notice, _ = take(client, leader)
    assert notice["task"]["channel"] == private["channel"]
    assert "PRIVATE_FAILURE_DETAIL" in notice["task"]["prompt"]
    assert "PRIVATE_FAILURE_DETAIL" not in json.dumps(store.snapshot(project["id"]))
