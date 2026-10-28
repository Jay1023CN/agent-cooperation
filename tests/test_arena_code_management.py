"""Independent checks of the boundary between an agent and its supervising worker."""

import pytest
from fastapi.testclient import TestClient

from arena.api import create_app


@pytest.fixture
def code_workspace(tmp_path):
    app = create_app(tmp_path / "arena")
    owner = (tmp_path / "arena" / "owner-token.txt").read_text(encoding="utf-8")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + owner
        response = client.post(
            "/api/projects",
            json={
                "name": "Code delivery fixture",
                "repo": "https://github.com/fixture/arena-example",
                "requirements": ["The implementation matches the reviewed commit"],
            },
        )
        assert response.status_code == 201, response.text
        project = response.json()
        response = client.post("/api/invites", json={"name": "Fixture machine", "kind": "machine"})
        assert response.status_code == 201, response.text
        machine_auth = {
            "Authorization": "Bearer " + response.json()["token"],
            "X-Arena-Worker-Protocol": "2",
        }
        response = client.post(
            "/api/machines/register",
            headers=machine_auth,
            json={
                "name": "Fixture machine",
                "os": "fixture",
                "slots": 2,
                "agents": [
                    {
                        "key": role,
                        "name": "Fixture " + role,
                        "provider": "fixture",
                        "roles": [role],
                        "capabilities": ["code"],
                    }
                    for role in ("worker", "reviewer")
                ],
            },
        )
        assert response.status_code == 200, response.text
        yield client, app.state.store, project, machine_auth


def make_work(client, project):
    response = client.post(
        f'/api/projects/{project["id"]}/tasks',
        json={"title": "Implement change", "prompt": "Implement the requested change"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def take_job(client, machine_auth):
    response = client.post("/api/machines/claim", headers=machine_auth, json={})
    assert response.status_code == 200, response.text
    job = response.json()
    assert job is not None
    assert job["agent_token"] != job["run_token"]
    return job


def auth(job, *, agent=False):
    return {"Authorization": "Bearer " + job["agent_token" if agent else "run_token"]}


def test_agent_can_read_context_and_delegate_but_cannot_renew_or_complete(code_workspace):
    client, store, project, machine_auth = code_workspace
    work = make_work(client, project)
    job = take_job(client, machine_auth)
    assert job["task"]["id"] == work["id"]
    agent_auth = auth(job, agent=True)

    context = client.get("/api/runs/context", headers=agent_auth)
    assert context.status_code == 200, context.text
    assert context.json()["task"]["id"] == work["id"]
    child = client.post(
        "/api/runs/tasks",
        headers=agent_auth,
        json={"title": "Independent subtask", "prompt": "Implement a related component"},
    )
    assert child.status_code == 201, child.text
    assert child.json()["parent_id"] == work["id"]

    lease_before = store.task(work["id"])["lease_until"]
    assert client.post("/api/runs/heartbeat", headers=agent_auth, json={}).status_code == 410
    forged = client.post(
        "/api/runs/complete",
        headers=agent_auth,
        json={"result": "Bypass the supervising worker", "evidence": {"commit": "f" * 40}},
    )
    assert forged.status_code == 410, forged.text
    current = store.task(work["id"])
    assert current["status"] == "running"
    assert current["lease_until"] == lease_before
    assert current["evidence"] == {}

    assert client.post("/api/runs/heartbeat", headers=auth(job), json={}).status_code == 200
    completed = client.post(
        "/api/runs/complete", headers=auth(job), json={"result": "Worker confirmed its local execution"}
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "coordinating"


@pytest.mark.parametrize("reviewed_commit", [None, "b" * 40, "a" * 40])
def test_acceptance_requires_supervisor_confirmation_of_the_exact_source_commit(code_workspace, reviewed_commit):
    client, store, project, machine_auth = code_workspace
    work = make_work(client, project)
    execution = take_job(client, machine_auth)
    source_commit = "a" * 40
    completion = client.post(
        "/api/runs/complete",
        headers=auth(execution),
        json={"result": "Implementation ready", "evidence": {"commit": source_commit, "delivery": "github"}},
    )
    assert completion.status_code == 200, completion.text
    assert completion.json()["status"] == "in_review"

    review = take_job(client, machine_auth)
    assert review["task"]["kind"] == "reviewer"
    assert review["source"]["id"] == work["id"]
    assert review["agent"]["id"] != execution["agent"]["id"]
    report = client.post(
        "/api/runs/review",
        headers=auth(review, agent=True),
        json={"decision": "accept", "summary": "Acceptance checks passed", "evidence": ["Fixture verification"]},
    )
    assert report.status_code == 200, report.text
    assert store.task(work["id"])["status"] == "in_review"
    forged = client.post(
        "/api/runs/complete",
        headers=auth(review, agent=True),
        json={"result": "Agent attempts to accept its own report", "evidence": {"commit": source_commit}},
    )
    assert forged.status_code == 410, forged.text
    assert store.task(work["id"])["status"] == "in_review"

    evidence = {"commit": reviewed_commit} if reviewed_commit else {}
    finalized = client.post(
        "/api/runs/complete",
        headers=auth(review),
        json={"result": "Supervising worker's verification result", "evidence": evidence},
    )
    assert finalized.status_code == 200, finalized.text
    if reviewed_commit == source_commit:
        assert finalized.json()["status"] == "done"
        assert store.task(work["id"])["status"] == "done"
        assert store.task(work["id"])["review"]["decision"] == "accept"
    else:
        assert finalized.json()["status"] == "blocked"
        assert store.task(work["id"])["status"] == "in_review"
        assert store.task(work["id"])["review"] is None


def test_cancel_and_retry_revoke_the_previous_agents_action_token(code_workspace):
    client, _, project, machine_auth = code_workspace
    work = make_work(client, project)
    previous = take_job(client, machine_auth)
    assert client.get("/api/runs/context", headers=auth(previous, agent=True)).status_code == 200
    assert client.post(f'/api/tasks/{work["id"]}/cancel').status_code == 200
    assert client.get("/api/runs/context", headers=auth(previous, agent=True)).status_code == 410
    assert client.post(f'/api/tasks/{work["id"]}/retry').status_code == 200
    current = take_job(client, machine_auth)
    assert current["task"]["id"] == work["id"]
    assert current["agent_token"] != previous["agent_token"]
    assert client.get("/api/runs/context", headers=auth(previous, agent=True)).status_code == 410
    assert client.get("/api/runs/context", headers=auth(current, agent=True)).status_code == 200
