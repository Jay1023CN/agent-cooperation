import pytest

from agent_link.multica_team import configure_team


def test_setup_checks_existing_names_before_mutating(monkeypatch):
    calls = []

    def call(_command, _profile, *args):
        calls.append(args)
        if args[:2] == ("agent", "list"):
            return [{"name": "Team · 负责人", "archived_at": None}]
        if args[:2] == ("squad", "list"):
            return []
        raise AssertionError("Must not mutate on a name collision")

    monkeypatch.setattr("agent_link.multica_team.call_multica", call)
    with pytest.raises(ValueError, match="already exists"):
        configure_team("multica", None, "Team", "r1", "r2")
    assert all(args[1] == "list" for args in calls)


def test_setup_requires_online_runtimes(monkeypatch):
    calls = []

    def call(_command, _profile, *args):
        calls.append(args)
        if args[:2] in (("agent", "list"), ("squad", "list")):
            return []
        if args[:2] == ("runtime", "list"):
            return [{"id": "r1", "status": "online"}, {"id": "r2", "status": "offline"}]
        raise AssertionError("Must not create a team when a runtime is offline")

    monkeypatch.setattr("agent_link.multica_team.call_multica", call)
    with pytest.raises(ValueError, match="offline"):
        configure_team("multica", None, "Team", "r1", "r2")
    assert all(args[1] == "list" for args in calls)
