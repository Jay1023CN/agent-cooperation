import json
import os
import subprocess
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from arena.api import create_app
from arena.github import center_environment


class FakeGitHub:
    """Stands in for github.com: token exchange, refresh, and /user."""

    def __init__(self):
        self.users = {"code-octo": {"id": 7, "login": "Octo", "name": "Octo Cat"}}
        self.refresh_token = True
        self.requests = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        if request.url.path == "/login/oauth/access_token":
            form = parse_qs(request.content.decode())
            if form.get("grant_type") == ["refresh_token"]:
                return httpx.Response(200, json=self.tokens("refreshed"))
            code = form["code"][0]
            if code not in self.users:
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json=self.tokens(code))
        if request.url.path == "/user":
            token = request.headers["authorization"].split()[-1]
            code = token.removeprefix("access-")
            return httpx.Response(200, json=self.users.get(code, self.users["code-octo"]))
        return httpx.Response(404)

    def tokens(self, code):
        body = {"access_token": f"access-{code}", "expires_in": 28800}
        if self.refresh_token:
            body.update(refresh_token=f"refresh-{code}", refresh_token_expires_in=15897600)
        return body


@pytest.fixture
def arena(tmp_path):
    app = create_app(tmp_path / "arena")
    github = FakeGitHub()
    app.state.github_web.http = httpx.Client(transport=httpx.MockTransport(github))
    token = (tmp_path / "arena" / "owner-token.txt").read_text(encoding="utf-8")
    with TestClient(app, follow_redirects=False) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, app, github


def configure(client):
    response = client.put("/api/github/app", json={"client_id": "Iv23liTEST", "client_secret": "secret-value-1"})
    assert response.status_code == 200, response.text


def sign_in(client, code, path="/api/github/login"):
    start = client.get(path)
    assert start.status_code == 303
    target = urlsplit(start.headers["location"])
    assert target.netloc == "github.com"
    query = parse_qs(target.query)
    assert query["client_id"] == ["Iv23liTEST"]
    assert query["redirect_uri"] == ["http://testserver/api/github/callback"]
    return client.get("/api/github/callback", params={"code": code, "state": query["state"][0]})


def error_of(response):
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query).get("github_error", [""])[0]


def test_login_is_hidden_until_the_app_is_configured(arena):
    client, _, _ = arena
    assert client.get("/api/github/status").json() == {"enabled": False}
    assert "还没有配置" in error_of(client.get("/api/github/login"))
    configure(client)
    assert client.get("/api/github/status").json() == {"enabled": True}
    settings = client.get("/api/github/app").json()
    assert settings["configured"] and "secret" not in json.dumps(settings)


def test_invited_partner_signs_in_with_github(arena):
    client, app, _ = arena
    configure(client)
    partner = client.post("/api/invites", json={"name": "Partner", "kind": "member", "github_login": "octo"}).json()
    owner_auth = client.headers.pop("Authorization")
    response = sign_in(client, "code-octo")
    assert response.headers["location"] == "/?github=connected"
    assert client.get("/api/me").json()["id"] == partner["id"]
    client.headers["Authorization"] = owner_auth
    listed = {item["id"]: item for item in client.get("/api/identities").json()["identities"]}
    assert listed[partner["id"]]["github"] == {"github_login": "Octo", "connected": True}
    assert app.state.github_web.public(partner["id"])["git_email"] == "7+Octo@users.noreply.github.com"


def test_unknown_account_forged_state_and_non_expiring_tokens_are_refused(arena):
    client, _, github = arena
    configure(client)
    client.headers.pop("Authorization")
    assert "还没有加入" in error_of(sign_in(client, "code-octo"))
    assert "无效" in error_of(client.get("/api/github/callback", params={"code": "code-octo", "state": "forged"}))
    github.refresh_token = False
    assert "Expire user authorization tokens" in error_of(sign_in(client, "code-octo"))


def test_owner_connects_github_and_tasks_get_a_center_credential(arena):
    client, app, _ = arena
    configure(client)
    owner = client.get("/api/me").json()["id"]
    assert sign_in(client, "code-octo", "/api/github/connect").headers["location"] == "/?github=connected"
    project = client.post(
        "/api/projects",
        json={
            "name": "作品",
            "repo": "https://github.com/octo/demo",
            "policy": {"commit_as": owner, "push_branches": True},
        },
    ).json()
    client.post(f"/api/projects/{project['id']}/tasks", json={"title": "页面", "prompt": "做页面"})
    machine = client.post("/api/invites", json={"name": "PC", "kind": "machine"}).json()["token"]
    auth = {"Authorization": f"Bearer {machine}", "X-Arena-Worker-Protocol": "2"}
    client.post(
        "/api/machines/register",
        headers=auth,
        json={
            "name": "PC",
            "os": "Windows",
            "agents": [{"key": "w", "name": "W", "provider": "fixture", "roles": ["worker"], "capabilities": ["code"]}],
        },
    )
    job = client.post("/api/machines/claim", headers=auth, json={}).json()
    identity = job["commit_identity"]
    assert identity["source"] == "center" and identity["member_id"] == owner
    assert identity["token"] == "access-code-octo" and identity["git_email"] == "7+Octo@users.noreply.github.com"


def test_expiring_token_is_refreshed_before_it_is_handed_out(arena):
    client, app, github = arena
    configure(client)
    owner = client.get("/api/me").json()["id"]
    sign_in(client, "code-octo", "/api/github/connect")
    web = app.state.github_web
    with web.connect() as db:
        db.execute("UPDATE accounts SET access_expires=? WHERE identity_id=?", (time.time() + 60, owner))
    credential = web.push_credential(owner)
    assert credential["token"] == "access-refreshed"
    assert web.push_credential(owner)["token"] == "access-refreshed"
    assert sum(b"grant_type=refresh_token" in r.content for r in github.requests) == 1


def test_revoking_a_member_forgets_their_github_tokens(arena):
    client, app, _ = arena
    configure(client)
    partner = client.post("/api/invites", json={"name": "Partner", "kind": "member", "github_login": "octo"}).json()
    owner_auth = client.headers.pop("Authorization")
    sign_in(client, "code-octo")
    client.cookies.clear()
    client.headers["Authorization"] = owner_auth
    assert client.delete(f"/api/identities/{partner['id']}").status_code == 200
    assert app.state.github_web.public(partner["id"]) is None


def test_center_credential_reaches_git_only_through_the_helper(tmp_path):
    env, public = center_environment(
        {
            "member_id": "a" * 32,
            "github_id": 7,
            "github_login": "Octo",
            "git_name": "Octo Cat",
            "git_email": "7+Octo@users.noreply.github.com",
            "token": "access-secret",
            "source": "center",
        }
    )
    assert public["git_email"] == "7+Octo@users.noreply.github.com" and "token" not in public
    fill = subprocess.run(
        ["git", "credential", "fill"],
        cwd=tmp_path,
        env=env,
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    fields = dict(line.split("=", 1) for line in fill.stdout.splitlines() if "=" in line)
    assert fields["username"] == "Octo" and fields["password"] == "access-secret"
    assert env["GIT_AUTHOR_EMAIL"] == "7+Octo@users.noreply.github.com"
    assert "ARENA_GITHUB_TOKEN" not in os.environ


def test_credential_errors_from_the_center_stop_the_run():
    from agent_link.github_identity import GitHubIdentityError

    with pytest.raises(GitHubIdentityError, match="重新用 GitHub 登录"):
        center_environment({"credential_error": "GitHub 授权已过期，请这位成员重新用 GitHub 登录一次"})
