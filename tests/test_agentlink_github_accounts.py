import pytest

from agent_link.github_accounts import GitHubAccounts
from agent_link.github_identity import GitHubIdentityError
from agent_link.team_routing import TeamProfiles


def test_member_bindings_are_distinct_and_recheck_the_actual_login(tmp_path, monkeypatch):
    accounts = GitHubAccounts(str(tmp_path / "accounts.db"))
    alice_dir = tmp_path / "alice"
    bob_dir = tmp_path / "bob"
    alice_dir.mkdir()
    bob_dir.mkdir()
    values = {
        "alice": {
            "github_id": 1,
            "github_login": "alice",
            "git_name": "Alice",
            "git_email": "alice@example.invalid",
            "email_source": "test",
        },
        "bob": {
            "github_id": 2,
            "github_login": "bob",
            "git_name": "Bob",
            "git_email": "bob@example.invalid",
            "email_source": "test",
        },
    }
    monkeypatch.setattr(
        "agent_link.github_accounts.read_identity", lambda path, gh="gh": values[path.split("\\")[-1].split("/")[-1]]
    )
    accounts.bind("member-a", str(alice_dir))
    accounts.bind("member-b", str(bob_dir))
    assert accounts.resolve("member-a")["github_login"] == "alice"
    assert accounts.resolve("member-b")["github_login"] == "bob"
    assert accounts.git_environment("member-a")["GIT_AUTHOR_EMAIL"] == "alice@example.invalid"
    profiles = TeamProfiles.model_validate(
        {"members": [{"id": "member-a", "name": "A"}, {"id": "member-b", "name": "B"}], "machines": [], "agents": []}
    )
    profiles = accounts.apply_to_profiles(profiles, "member-a")
    assert profiles.members[0].github_login == "alice"
    assert profiles.members[1].github_login is None
    values["alice"] = values["bob"]
    with pytest.raises(GitHubIdentityError, match="differs"):
        accounts.resolve("member-a")
    with pytest.raises(GitHubIdentityError, match="already bound"):
        accounts.bind("member-a", str(alice_dir))
