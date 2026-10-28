"""Assignment advice uses declared inputs and never claims or creates work."""

import time

import pytest
from pydantic import ValidationError

from agent_link.assignment import Assignment, recommend


def candidate(identifier, **updates):
    return {
        "agent_id": identifier,
        "name": identifier,
        "machine_id": "machine-1",
        "provider": identifier,
        "model": "configured-model",
        "roles": ["worker", "reviewer"],
        "capabilities": ["code"],
        "online": True,
        "dispatchable": True,
        "observed_at": 1000,
        "agent_active": 0,
        "agent_limit": 1,
        "machine_active": 0,
        "machine_limit": 2,
        "profile": {"quality": 3, "speed": 3, "economy": 3, "max_difficulty": "deep"},
        **updates,
    }


def advice(*candidates, task=None, policy=None):
    request = Assignment.model_validate(
        {
            "task": {"kind": "worker", "capability": "code", "state": "new", "inputs_verified": True, **(task or {})},
            "policy": policy or {},
            "candidates": list(candidates),
        }
    )
    return recommend(request, now=1000)


@pytest.mark.parametrize("routing,winner", [("quality", "quality"), ("economy", "budget")])
def test_quality_and_economy_change_ranking_without_claiming_a_run(routing, winner):
    quality = candidate("quality", profile={"quality": 5, "economy": 1, "max_difficulty": "deep"})
    budget = candidate("budget", profile={"quality": 1, "economy": 5, "max_difficulty": "deep"})
    result = advice(quality, budget, policy={"routing": routing})
    assert result["recommended_agent_id"] == winner
    assert "不预留名额或派发任务" in result["note"]
    assert "未推算真实价格" in result["ranked"][0]["reason"]


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"online": False}, "离线"),
        ({"online": None}, "未知"),
        ({"dispatchable": None}, "可执行状态未知"),
        ({"observed_at": 800}, "已过期"),
        ({"observed_at": 1020}, "已过期"),
        ({"agent_active": None}, "并发状态未知"),
        ({"agent_active": 1, "machine_active": 1}, "并发已满"),
        ({"machine_active": 2}, "并发已满"),
        ({"capabilities": ["research"]}, "所需能力"),
        ({"profile": {"max_difficulty": "deep", "quota_state": "exhausted"}}, "额度"),
        ({"profile": {"max_difficulty": "deep", "paused_until": 2000}}, "暂停"),
        ({"profile": {"max_difficulty": "routine"}}, "难度"),
        ({"profile": {}}, "难度未声明"),
    ],
)
def test_ineligible_candidate_never_wins_even_with_high_preference(updates, reason):
    result = advice(candidate("invalid", machine_id="other-machine", **updates), candidate("available"))
    assert result["recommended_agent_id"] == "available"
    assert reason in " ".join(result["excluded"][0]["reasons"])


def test_independent_reviewer_and_local_commit_requirements_are_hard_constraints():
    task = {
        "kind": "reviewer",
        "capability": "code",
        "executor_agent_id": "author",
        "local_input_machine_ids": ["machine-1"],
    }
    result = advice(candidate("author"), candidate("remote", machine_id="machine-2"), candidate("reviewer"), task=task)
    assert result["recommended_agent_id"] == "reviewer"
    reasons = {item["agent_id"]: item["reasons"] for item in result["excluded"]}
    assert "不同 agent ID" in reasons["author"][0]
    assert "其他电脑" in reasons["remote"][0]


def test_explicit_request_never_falls_back_and_ties_are_stable():
    result = advice(
        candidate("chosen", online=False),
        candidate("other"),
        task={"kind": "worker", "capability": "code", "requested_agent_id": "chosen"},
    )
    assert result["recommended_agent_id"] is None
    assert not result["ranked"]
    assert advice(candidate("z"), candidate("a"))["recommended_agent_id"] == "a"


def test_local_inputs_from_multiple_machines_are_not_treated_as_accessible():
    result = advice(
        candidate("one"),
        candidate("two", machine_id="machine-2"),
        task={"local_input_machine_ids": ["machine-1", "machine-2"]},
    )
    assert result["recommended_agent_id"] is None
    assert all("其他电脑" in item["reasons"][0] for item in result["excluded"])


@pytest.mark.parametrize("state", ["unknown", "running", "outcome_unknown", "completed"])
def test_task_without_an_eligible_state_is_not_recommended(state):
    result = advice(candidate("available"), task={"state": state})
    assert result["recommended_agent_id"] is None
    assert "不能自动重派" in result["task_blockers"][0]


def test_unverified_input_provenance_is_not_recommended():
    result = advice(candidate("available"), task={"inputs_verified": False})
    assert result["recommended_agent_id"] is None
    assert "位置尚未完整核对" in result["task_blockers"][0]


def test_recommendation_reports_snapshot_and_expiry():
    result = advice(candidate("available", observed_at=950))
    assert result["generated_at"] == 1000
    assert result["ranked"][0]["observed_at"] == 950
    assert result["valid_until"] == result["ranked"][0]["valid_until"] == 1070


def test_context_capacity_and_unknown_quota_are_not_invented():
    result = advice(
        candidate("small", profile={"max_difficulty": "deep", "context_window": 100}),
        candidate("unknown"),
        task={"kind": "worker", "capability": "code", "context_tokens": 1000},
    )
    assert result["recommended_agent_id"] == "unknown"
    assert "上下文容量不足" in result["excluded"][0]["reasons"][0]
    assert "上下文容量未声明" in result["ranked"][0]["reason"]
    assert "剩余额度未知" in result["ranked"][0]["reason"]


def test_invalid_or_ambiguous_input_is_rejected():
    task = {"kind": "worker", "capability": "code"}
    with pytest.raises(ValidationError, match="unique"):
        Assignment.model_validate({"task": task, "candidates": [candidate("same"), candidate("same")]})
    with pytest.raises(ValidationError, match="at most 12"):
        Assignment.model_validate({"task": task, "candidates": [candidate("x", accepted_recent=8, failed_recent=5)]})
    with pytest.raises(ValidationError, match="inconsistent concurrency"):
        Assignment.model_validate({"task": task, "candidates": [candidate("x"), candidate("y", machine_active=1)]})
    with pytest.raises(ValidationError, match="cannot exceed"):
        Assignment.model_validate({"task": task, "candidates": [candidate("x", agent_active=1)]})
    with pytest.raises(ValidationError):
        Assignment.model_validate({"task": task, "candidates": [candidate("x", online="yes")]})
    with pytest.raises(ValidationError, match="executing agent ID"):
        Assignment.model_validate({"task": {"kind": "reviewer", "capability": "code"}, "candidates": []})


@pytest.mark.asyncio
async def test_assignment_tool_is_exposed_by_multica_mcp_config(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from agent_link.multica import mcp_config

    config = mcp_config()["mcpServers"]["pal-agent-link"]
    parameters = StdioServerParameters(**config, cwd=str(tmp_path))
    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "plan_agent_assignment",
            {
                "request": {
                    "task": {"kind": "worker", "capability": "code", "state": "new", "inputs_verified": True},
                    "candidates": [candidate("online", observed_at=time.time())],
                }
            },
        )
        assert not result.isError, result.content
        import json

        data = result.structuredContent or json.loads(result.content[0].text)
        assert data["recommended_agent_id"] == "online"
