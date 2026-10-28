import pytest
from pydantic import ValidationError

from agent_link.assignment import Policy, Task
from agent_link.server import Agents, build_server
from agent_link.team_routing import TeamProfiles, recommend_team


def fixtures():
    profiles = TeamProfiles.model_validate(
        {
            "members": [
                {"id": "alice", "name": "Alice", "github_login": "alice-gh", "git_email": "alice@example.invalid"},
                {"id": "bob", "name": "Bob"},
            ],
            "machines": [{"daemon_id": "computer", "slots": 3}],
            "agents": [
                {
                    "agent_id": "fast",
                    "owner_id": "alice",
                    "machine_id": "computer",
                    "roles": ["worker"],
                    "capabilities": ["code"],
                    "quality": 3,
                    "speed": 5,
                    "economy": 5,
                    "max_difficulty": "standard",
                    "preferred_for": ["code"],
                },
                {
                    "agent_id": "deep",
                    "owner_id": "bob",
                    "machine_id": "computer",
                    "roles": ["worker"],
                    "capabilities": ["code"],
                    "quality": 5,
                    "speed": 2,
                    "economy": 1,
                    "max_difficulty": "deep",
                },
            ],
        }
    )
    agents = [
        {"id": "fast", "name": "Fast", "runtime_id": "r1", "max_concurrent_tasks": 1},
        {"id": "deep", "name": "Deep", "runtime_id": "r2", "max_concurrent_tasks": 2},
    ]
    runtimes = [
        {"id": "r1", "daemon_id": "computer", "status": "online", "provider": "qoder"},
        {"id": "r2", "daemon_id": "computer", "status": "online", "provider": "claude"},
    ]
    return profiles, agents, runtimes


def test_declared_cost_preference_and_owner_are_used():
    profiles, agents, runtimes = fixtures()
    task = Task(kind="worker", capability="code", state="new", inputs_verified=True)
    result = recommend_team(
        task, Policy(routing="economy"), profiles, agents, runtimes, {"fast": [], "deep": []}, now=10
    )
    assert result["recommended_agent_id"] == "fast"
    assert result["ranked"][0]["owner"]["github_login"] == "alice-gh"
    assert "未推算真实价格" in result["ranked"][0]["reason"]


def test_live_capacity_and_difficulty_exclude_unsuitable_members():
    profiles, agents, runtimes = fixtures()
    task = Task(kind="worker", capability="code", difficulty="deep", state="new", inputs_verified=True)
    result = recommend_team(task, Policy(), profiles, agents, runtimes, {"fast": [], "deep": []}, now=10)
    assert result["recommended_agent_id"] == "deep"
    result = recommend_team(
        task, Policy(), profiles, agents, runtimes, {"fast": [], "deep": [{"status": "running"}] * 2}, now=10
    )
    assert result["recommended_agent_id"] is None


def test_incomplete_run_history_and_machine_mismatch_do_not_look_available():
    profiles, agents, runtimes = fixtures()
    task = Task(kind="worker", capability="code", state="new", inputs_verified=True)
    result = recommend_team(task, Policy(), profiles, agents, runtimes, {"fast": [{}] * 200, "deep": []}, now=10)
    assert result["recommended_agent_id"] is None
    runtimes[0]["daemon_id"] = "another-computer"
    result = recommend_team(task, Policy(), profiles, agents, runtimes, {"deep": []}, now=10)
    assert result["recommended_agent_id"] == "deep"


def test_owner_and_machine_references_must_exist():
    profiles, _, _ = fixtures()
    data = profiles.model_dump()
    data["agents"][0]["owner_id"] = "unknown"
    with pytest.raises(ValidationError, match="reference"):
        TeamProfiles.model_validate(data)


@pytest.mark.asyncio
async def test_mcp_uses_declared_profiles_and_supplied_observations(tmp_path, monkeypatch):
    profiles, agents, runtimes = fixtures()
    path = tmp_path / "team.json"
    path.write_text(profiles.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("AGENT_COOPERATION_PROFILES", str(path))
    server = build_server(Agents())
    response = await server.call_tool(
        "recommend_configured_team",
        {
            "task": {"kind": "worker", "capability": "code", "state": "new", "inputs_verified": True},
            "policy": {"routing": "economy"},
            "agents": agents,
            "runtimes": runtimes,
            "runs": {"fast": [], "deep": []},
        },
    )
    assert "fast" in str(response)
    assert "alice-gh" in str(response)
