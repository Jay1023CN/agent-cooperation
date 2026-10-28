import subprocess

import pytest
from fastapi.testclient import TestClient

from arena.api import create_app
from arena.worker import WorkerError, capture_git, commit_message


@pytest.fixture
def arena(tmp_path):
    app = create_app(tmp_path / "arena")
    token = (tmp_path / "arena" / "owner-token.txt").read_text(encoding="utf-8")
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, app.state.store


def owner_id(client):
    return client.get("/api/me").json()["id"]


def machine(client, name, github_members=()):
    token = client.post("/api/invites", json={"name": name, "kind": "machine"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}", "X-Arena-Worker-Protocol": "2"}
    response = client.post(
        "/api/machines/register",
        headers=auth,
        json={
            "name": name,
            "os": "Windows",
            "slots": 1,
            "agents": [
                {
                    "key": "w",
                    "name": name + " agent",
                    "provider": "fixture",
                    "roles": ["worker"],
                    "capabilities": ["code"],
                }
            ],
            "github_members": list(github_members),
        },
    )
    assert response.status_code == 200, response.text
    return auth


def project_with_identity(client, member):
    response = client.post("/api/projects", json={"name": "作品", "policy": {"commit_as": member}})
    assert response.status_code == 201, response.text
    return response.json()


def worker_task(client, project_id):
    response = client.post(f"/api/projects/{project_id}/tasks", json={"title": "实现页面", "prompt": "做页面"})
    assert response.status_code == 201, response.text
    return response.json()


def test_commit_identity_must_be_an_active_member(arena):
    client, _ = arena
    response = client.post("/api/projects", json={"name": "x", "policy": {"commit_as": "0" * 32}})
    assert response.status_code == 422
    machine_token = client.post("/api/invites", json={"name": "pc", "kind": "machine"}).json()
    response = client.post("/api/projects", json={"name": "x", "policy": {"commit_as": machine_token["id"]}})
    assert response.status_code == 422


def test_task_waits_for_a_machine_where_the_member_logged_in(arena):
    client, store = arena
    member = owner_id(client)
    plain = machine(client, "Plain PC")
    p = project_with_identity(client, member)
    task = worker_task(client, p["id"])
    assert client.post("/api/machines/claim", headers=plain, json={}).json() is None
    assert "pal-arena github login" in store.task(task["id"])["routing_reason"]

    bound = machine(client, "Bound PC", [{"member_id": member, "github_id": 42, "github_login": "octo-member"}])
    assert client.post("/api/machines/claim", headers=plain, json={}).json() is None
    job = client.post("/api/machines/claim", headers=bound, json={}).json()
    assert job["commit_identity"] == {"member_id": member, "github_id": 42, "github_login": "octo-member"}
    assert "machine_github_members" not in job["agent"]
    machines = client.get("/api/state").json()["machines"]
    assert {m["name"]: m["github_members"] for m in machines}["Bound PC"] == [
        {"member_id": member, "github_login": "octo-member"}
    ]


@pytest.mark.parametrize(
    "identity, expected",
    [(None, "failed"), ("other", "failed"), ("member", "in_review")],
)
def test_completion_requires_the_project_commit_identity(arena, identity, expected):
    client, store = arena
    member = owner_id(client)
    auth = machine(client, "Bound PC", [{"member_id": member, "github_id": 42, "github_login": "octo"}])
    p = project_with_identity(client, member)
    task = worker_task(client, p["id"])
    job = client.post("/api/machines/claim", headers=auth, json={}).json()
    evidence = {"branch": "arena/x/1", "commit": "a" * 40}
    if identity:
        evidence["commit_identity"] = {"member_id": member if identity == "member" else "b" * 32}
    response = client.post(
        "/api/runs/complete",
        headers={"Authorization": "Bearer " + job["run_token"]},
        json={"result": "完成", "evidence": evidence},
    )
    assert response.status_code == 200, response.text
    assert store.task(task["id"])["status"] == expected


def git(path, *args, env=None):
    return subprocess.run(
        ["git", *args], cwd=path, env=env, check=True, capture_output=True, encoding="utf-8"
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "arena/t/1")
    git(path, "config", "user.name", "Machine")
    git(path, "config", "user.email", "machine@localhost")
    (path / "a.txt").write_text("base", encoding="utf-8")
    git(path, "add", "a.txt")
    git(path, "commit", "-q", "-m", "base")
    return path


def member_identity():
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "Member",
        "GIT_AUTHOR_EMAIL": "1+member@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Member",
        "GIT_COMMITTER_EMAIL": "1+member@users.noreply.github.com",
    }
    public = {"member_id": "c" * 32, "github_login": "member", "git_email": "1+member@users.noreply.github.com"}
    return env, public


def test_capture_commits_as_the_member_and_records_it(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("changed", encoding="utf-8")
    job = {"task": {"id": "t1", "title": "实现页面"}}
    message = commit_message(job, {"name": "Qoder", "provider": "qoder"}, "Windows PC")
    evidence = capture_git(repo, "arena/t/1", {}, base_commit=base, identity=member_identity(), message=message)
    assert git(repo, "log", "-1", "--format=%an <%ae> %cn <%ce>") == (
        "Member <1+member@users.noreply.github.com> Member <1+member@users.noreply.github.com>"
    )
    body = git(repo, "log", "-1", "--format=%B")
    assert body.startswith("实现页面") and "Arena-Agent: Qoder (qoder)" in body and "Arena-Machine: Windows PC" in body
    assert evidence["commit_identity"]["github_login"] == "member"


def test_capture_refuses_agent_commits_under_another_identity(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("agent", encoding="utf-8")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "agent commit with machine identity")
    with pytest.raises(WorkerError, match="not authored as the project's GitHub member"):
        capture_git(repo, "arena/t/1", {}, base_commit=base, identity=member_identity())


def test_capture_without_identity_keeps_previous_behaviour(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("changed", encoding="utf-8")
    evidence = capture_git(repo, "arena/t/1", {}, base_commit=base)
    assert "commit_identity" not in evidence
    assert git(repo, "log", "-1", "--format=%ae %s") == "machine@localhost Complete Arena task"
