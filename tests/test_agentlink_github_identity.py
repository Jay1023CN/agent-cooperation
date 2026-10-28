import json
import subprocess

import pytest

from agent_link.github_identity import (
    GitHubIdentityError,
    bind_member_identity,
    git_environment,
    identity_from_user,
    isolated_environment,
    login_command,
    read_identity,
)
from agent_link.team_routing import TeamProfiles


def test_identity_uses_github_account_and_documented_private_email():
    identity = identity_from_user({"id": 123, "login": "alice", "name": "Alice", "created_at": "2022-01-01T00:00:00Z"})
    assert identity["github_login"] == "alice"
    assert identity["git_email"] == "123+alice@users.noreply.github.com"
    old = identity_from_user({"id": 4, "login": "old", "created_at": "2012-01-01T00:00:00Z"})
    assert old["git_email"] is None


def test_identity_does_not_use_other_member_environment_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    env = isolated_environment(str(tmp_path))
    assert env["GH_CONFIG_DIR"] == str(tmp_path.resolve())
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env
    command, login_env = login_command(str(tmp_path))
    assert "--web" in command and "--with-token" not in command
    assert "GH_PROMPT_DISABLED" not in login_env
    with pytest.raises(GitHubIdentityError):
        isolated_environment("relative")


def test_git_commit_uses_only_bound_member_identity(tmp_path, monkeypatch):
    config = tmp_path / "profile"
    repo = tmp_path / "repo"
    config.mkdir()
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "file.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "other@example.invalid")
    identity = {"git_name": "Alice", "git_email": "1+alice@users.noreply.github.com"}
    env = git_environment(str(config), identity)
    subprocess.run(["git", "commit", "-m", "test"], cwd=repo, env=env, check=True, capture_output=True)
    actual = subprocess.check_output(["git", "show", "-s", "--format=%an <%ae>|%cn <%ce>"], cwd=repo, text=True).strip()
    assert actual == "Alice <1+alice@users.noreply.github.com>|Alice <1+alice@users.noreply.github.com>"
    assert env["GIT_CONFIG_VALUE_0"] == "" and env["GIT_CONFIG_VALUE_1"] == "!gh auth git-credential"


def test_read_identity_queries_current_profile_without_printing_token(tmp_path, monkeypatch):
    def run(args, **kwargs):
        assert args[-1] == "user"
        assert kwargs["env"]["GH_CONFIG_DIR"] == str(tmp_path.resolve())
        return subprocess.CompletedProcess(
            args, 0, json.dumps({"id": 1, "login": "alice", "created_at": "2020-01-01T00:00:00Z"}), ""
        )

    monkeypatch.setattr("agent_link.github_identity.subprocess.run", run)
    assert read_identity(str(tmp_path))["git_email"] == "1+alice@users.noreply.github.com"


def test_bind_github_identity_to_only_the_authenticated_member():
    profiles = TeamProfiles.model_validate(
        {"members": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}], "machines": [], "agents": []}
    )
    identity = identity_from_user({"id": 123, "login": "alice", "created_at": "2022-01-01T00:00:00Z"})
    updated = bind_member_identity(profiles, "a", identity)
    assert updated.members[0].github_login == "alice"
    assert updated.members[0].git_email == "123+alice@users.noreply.github.com"
    assert updated.members[1].github_login is None
    assert profiles.members[0].github_login is None
    with pytest.raises(GitHubIdentityError, match="Another"):
        bind_member_identity(
            updated, "a", identity_from_user({"id": 456, "login": "bob", "created_at": "2022-01-01T00:00:00Z"})
        )
