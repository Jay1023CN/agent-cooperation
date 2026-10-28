"""Recommend Multica members using declared profiles and current runtime data."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .assignment import Assignment, Candidate, Policy, Profile, Task, recommend
from .multica_team import call_multica


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Member(ConfigModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    github_login: str | None = None
    github_id: int | None = None
    git_name: str | None = None
    git_email: str | None = None


class Machine(ConfigModel):
    daemon_id: str = Field(min_length=1)
    slots: int = Field(ge=1, le=100)


class AgentProfile(ConfigModel):
    agent_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    machine_id: str = Field(min_length=1, description="Multica daemon ID")
    roles: list[Literal["leader", "worker", "reviewer"]] = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    quality: int = Field(ge=1, le=5)
    speed: int = Field(ge=1, le=5)
    economy: int = Field(ge=1, le=5)
    max_difficulty: Literal["routine", "standard", "deep"]
    preferred_for: list[str] = Field(default_factory=list)
    quota_state: Literal["unknown", "available", "limited", "exhausted"] = "unknown"
    enabled: bool = True


class TeamProfiles(ConfigModel):
    members: list[Member]
    machines: list[Machine]
    agents: list[AgentProfile]

    @model_validator(mode="after")
    def validate_references(self):
        for label, values in (
            ("member", [item.id for item in self.members]),
            ("machine", [item.daemon_id for item in self.machines]),
            ("agent", [item.agent_id for item in self.agents]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {label} ID")
        members, machines = {x.id for x in self.members}, {x.daemon_id for x in self.machines}
        if any(a.owner_id not in members or a.machine_id not in machines for a in self.agents):
            raise ValueError("Every agent must reference a configured member and machine")
        return self


ACTIVE = {"queued", "dispatched", "running", "waiting_local_directory"}


def recommend_team(
    task: Task, policy: Policy, profiles: TeamProfiles, agents, runtimes, runs, *, now=None, observed_at=None
):
    """Use declared strengths and one complete Multica observation; do not dispatch."""
    if not all(
        isinstance(item, dict) and {"id", "runtime_id", "owner_id", "max_concurrent_tasks"} <= item.keys()
        for item in agents
    ):
        raise ValueError(
            "Agent observations need id, runtime_id, owner_id and max_concurrent_tasks; pass the CLI output intact."
        )
    if not all(
        isinstance(item, dict) and {"id", "daemon_id", "status", "provider"} <= item.keys() for item in runtimes
    ):
        raise ValueError("Runtime observations need id, daemon_id, status and provider; pass the CLI output intact.")
    now = time.time() if now is None else now
    observed_at = now if observed_at is None else observed_at
    by_agent = {item["id"]: item for item in agents}
    by_runtime = {item["id"]: item for item in runtimes}
    members = {item.id: item for item in profiles.members}
    limits = {item.daemon_id: item.slots for item in profiles.machines}
    machine_active = dict.fromkeys(limits, 0)
    machine_unknown = set()
    active = {}
    for agent in agents:
        runtime = by_runtime.get(agent.get("runtime_id"), {})
        machine = runtime.get("daemon_id")
        if machine not in limits or agent.get("archived_at"):
            continue
        history = runs.get(agent["id"])
        if not isinstance(history, list) or len(history) >= 200:
            machine_unknown.add(machine)
            active[agent["id"]] = None
            continue
        active[agent["id"]] = sum(item.get("status") in ACTIVE for item in history)
        machine_active[machine] += active[agent["id"]]
    candidates = []
    owner_mismatches = set()
    for declared in profiles.agents:
        agent = by_agent.get(declared.agent_id, {})
        runtime = by_runtime.get(agent.get("runtime_id"), {})
        matches_machine = runtime.get("daemon_id") == declared.machine_id
        owner_matches = agent.get("owner_id") == declared.owner_id
        if not owner_matches:
            owner_mismatches.add(declared.agent_id)
        online = runtime.get("status") == "online" and matches_machine
        candidates.append(
            Candidate(
                agent_id=declared.agent_id,
                name=agent.get("name") or declared.agent_id,
                machine_id=declared.machine_id,
                provider=runtime.get("provider") or "unknown",
                model=agent.get("model") or None,
                roles=declared.roles,
                capabilities=declared.capabilities,
                enabled=declared.enabled and not bool(agent.get("archived_at")) and owner_matches,
                online=online,
                dispatchable=online and bool(agent),
                observed_at=observed_at,
                agent_active=active.get(declared.agent_id),
                agent_limit=agent.get("max_concurrent_tasks"),
                machine_active=None if declared.machine_id in machine_unknown else machine_active[declared.machine_id],
                machine_limit=limits[declared.machine_id],
                profile=Profile(
                    quality=declared.quality,
                    speed=declared.speed,
                    economy=declared.economy,
                    max_difficulty=declared.max_difficulty,
                    preferred_for=declared.preferred_for,
                    quota_state=declared.quota_state,
                ),
            )
        )
    result = recommend(Assignment(task=task, policy=policy, candidates=candidates), now=now)
    for item in result["excluded"]:
        if item["agent_id"] in owner_mismatches:
            item["reasons"].append("配置的成员与 Multica 代理所有者不匹配，或代理所有者未知")
    declared_by_id = {item.agent_id: item for item in profiles.agents}
    for item in result["ranked"]:
        owner = members[declared_by_id[item["agent_id"]].owner_id]
        item["owner"] = owner.model_dump()
    result["note"] = (
        "能力和成本评分由成员填写；运行情况由调用者提供，工具无法独立认证来源或时效。"
        "机器并发仅统计快照中可见的代理；运行时在线不证明登录或额度可用。"
        "建议不会派发或保留名额；Git 作者信息不代表 GitHub 登录账号。"
    )
    return result


def live_recommend(task: Task, policy: Policy, config_path: str, command="multica", profile=None):
    profiles = TeamProfiles.model_validate_json(Path(config_path).read_text(encoding="utf-8"))
    observed_at = time.time()
    agents = call_multica(command, profile, "agent", "list")
    runtimes = call_multica(command, profile, "runtime", "list")
    relevant = {item.daemon_id for item in profiles.machines}
    runtime_machine = {item["id"]: item.get("daemon_id") for item in runtimes}
    runs = {}
    for agent in agents:
        if not agent.get("archived_at") and runtime_machine.get(agent.get("runtime_id")) in relevant:
            runs[agent["id"]] = call_multica(command, profile, "agent", "tasks", agent["id"], "--limit", "200")
    return recommend_team(task, policy, profiles, agents, runtimes, runs, observed_at=observed_at)
