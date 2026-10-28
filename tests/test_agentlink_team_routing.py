import json
import time

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
        {"id": "fast", "name": "Fast", "owner_id": "alice", "runtime_id": "r1", "max_concurrent_tasks": 1},
        {"id": "deep", "name": "Deep", "owner_id": "bob", "runtime_id": "r2", "max_concurrent_tasks": 2},
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


def test_claimed_owner_must_match_multica_owner():
    profiles, agents, runtimes = fixtures()
    agents[0]["owner_id"] = "bob"
    task = Task(kind="worker", capability="code", state="new", inputs_verified=True)
    result = recommend_team(
        task, Policy(routing="economy"), profiles, agents, runtimes, {"fast": [], "deep": []}, now=10
    )
    assert result["recommended_agent_id"] == "deep"
    assert any("所有者" in reason for reason in result["excluded"][0]["reasons"])


def test_missing_machine_identity_is_reported_not_misclassified_as_offline():
    profiles, agents, runtimes = fixtures()
    del runtimes[0]["daemon_id"]
    task = Task(kind="worker", capability="code", state="new", inputs_verified=True)
    with pytest.raises(ValueError, match="daemon_id"):
        recommend_team(task, Policy(), profiles, agents, runtimes, {"fast": [], "deep": []}, now=10)


def test_old_snapshot_is_not_renewed_by_reading_it_again():
    profiles, agents, runtimes = fixtures()
    task = Task(kind="worker", capability="code", state="new", inputs_verified=True)
    result = recommend_team(
        task, Policy(), profiles, agents, runtimes, {"fast": [], "deep": []}, now=200, observed_at=1
    )
    assert result["recommended_agent_id"] is None


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
            "observed_at": time.time(),
        },
    )
    assert "fast" in str(response)
    assert "alice-gh" in str(response)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {"agents": agents, "runtimes": runtimes, "runs": {"fast": [], "deep": []}, "observed_at": time.time()}
        ),
        encoding="utf-8",
    )
    response = await server.call_tool(
        "recommend_configured_team_from_file",
        {
            "task": {"kind": "worker", "capability": "code", "state": "new", "inputs_verified": True},
            "policy": {"routing": "economy"},
            "snapshot_path": str(snapshot),
        },
    )
    assert "fast" in str(response)


@pytest.mark.asyncio
async def test_mcp_reads_live_team_observations(tmp_path, monkeypatch):
    from agent_link import team_routing

    profiles, agents, runtimes = fixtures()
    path = tmp_path / "team.json"
    path.write_text(profiles.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("AGENT_COOPERATION_PROFILES", str(path))
    monkeypatch.setenv("AGENT_COOPERATION_MULTICA_COMMAND", "platform-cli")
    monkeypatch.setenv("AGENT_COOPERATION_MULTICA_PROFILE", "team-profile")
    calls = []

    def call(command, profile, *args):
        calls.append((command, profile, args))
        if args == ("agent", "list"):
            return agents
        if args == ("runtime", "list"):
            return runtimes
        assert args[:2] == ("agent", "tasks")
        return [{"status": "running"}] if args[2] == "fast" else []

    monkeypatch.setattr(team_routing, "call_multica", call)
    server = build_server(Agents())
    response = await server.call_tool(
        "recommend_live_team",
        {
            "task": {"kind": "worker", "capability": "code", "state": "new", "inputs_verified": True},
            "policy": {"routing": "economy"},
        },
    )
    assert '"recommended_agent_id":"deep"' in str(response).replace(" ", "")
    assert len(calls) == 4
    assert all(command == "platform-cli" and profile == "team-profile" for command, profile, _ in calls)


def test_live_observation_timestamp_is_taken_before_collection(tmp_path, monkeypatch):
    from agent_link import team_routing

    profiles, agents, runtimes = fixtures()
    path = tmp_path / "team.json"
    path.write_text(profiles.model_dump_json(), encoding="utf-8")
    observed = []

    def call(command, profile, *args):
        observed.append(time.time())
        return agents if args == ("agent", "list") else runtimes if args == ("runtime", "list") else []

    monkeypatch.setattr(team_routing, "call_multica", call)
    result = team_routing.live_recommend(
        Task(kind="worker", capability="code", state="new", inputs_verified=True), Policy(), str(path)
    )
    assert result["ranked"][0]["observed_at"] <= observed[0]
