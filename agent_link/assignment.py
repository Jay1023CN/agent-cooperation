"""Read-only, explainable assignment advice for Multica agent snapshots."""

from __future__ import annotations

import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arena.routing import evaluate


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Task(Input):
    kind: Literal["leader", "worker", "reviewer"]
    capability: str = Field(min_length=1, max_length=100)
    difficulty: Literal["routine", "standard", "deep"] = "standard"
    context_tokens: int = Field(default=0, ge=0, le=10_000_000)
    state: Literal["new", "queued", "retry_authorized", "running", "outcome_unknown", "completed", "unknown"] = (
        "unknown"
    )
    inputs_verified: bool = False
    executor_agent_id: str | None = Field(default=None, min_length=1, max_length=200)
    requested_agent_id: str | None = Field(default=None, min_length=1, max_length=200)
    local_input_machine_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=128
    )

    @model_validator(mode="after")
    def reviewer_requires_author(self):
        if self.kind == "reviewer" and not self.executor_agent_id:
            raise ValueError("Reviewer selection requires the executing agent ID.")
        return self


class Policy(Input):
    routing: Literal["balanced", "quality", "economy"] = "balanced"
    preferred_providers: list[str] = Field(default_factory=list, max_length=30)


class Profile(Input):
    quality: int = Field(default=3, ge=1, le=5)
    speed: int = Field(default=3, ge=1, le=5)
    economy: int = Field(default=3, ge=1, le=5)
    max_difficulty: Literal["routine", "standard", "deep"] | None = None
    context_window: int | None = Field(default=None, ge=1, le=10_000_000)
    quota_state: Literal["unknown", "available", "limited", "exhausted"] = "unknown"
    paused_until: float = Field(default=0, ge=0)
    preferred_for: list[str] = Field(default_factory=list, max_length=30)


class Candidate(Input):
    agent_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    machine_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    roles: list[Literal["leader", "worker", "reviewer"]] = Field(max_length=3)
    capabilities: list[str] = Field(max_length=30)
    enabled: bool = True
    online: bool | None = None
    dispatchable: bool | None = None
    observed_at: float | None = Field(default=None, ge=0)
    agent_active: int | None = Field(default=None, ge=0, le=1000)
    agent_limit: int | None = Field(default=None, ge=1, le=1000)
    machine_active: int | None = Field(default=None, ge=0, le=1000)
    machine_limit: int | None = Field(default=None, ge=1, le=1000)
    preference: int = Field(default=10, ge=0, le=1000)
    profile: Profile = Field(default_factory=Profile)
    accepted_recent: int = Field(default=0, ge=0, le=12)
    failed_recent: int = Field(default=0, ge=0, le=12)

    @model_validator(mode="after")
    def bound_history(self):
        if self.accepted_recent + self.failed_recent > 12:
            raise ValueError("Recent history must contain at most 12 outcomes.")
        if (
            self.agent_active is not None
            and self.machine_active is not None
            and self.agent_active > self.machine_active
        ):
            raise ValueError("Agent active runs cannot exceed its machine's active runs.")
        return self


class Assignment(Input):
    task: Task
    policy: Policy = Field(default_factory=Policy)
    candidates: list[Candidate] = Field(max_length=128)

    @model_validator(mode="after")
    def unique_agents(self):
        if len({c.agent_id for c in self.candidates}) != len(self.candidates):
            raise ValueError("Each candidate must have a unique Multica agent ID.")
        machines = {}
        for candidate in self.candidates:
            previous = machines.setdefault(candidate.machine_id, (candidate.machine_active, candidate.machine_limit))
            current = (candidate.machine_active, candidate.machine_limit)
            if any(a is not None and b is not None and a != b for a, b in zip(previous, current)):
                raise ValueError("Candidates on the same machine have inconsistent concurrency snapshots.")
            machines[candidate.machine_id] = tuple(a if a is not None else b for a, b in zip(previous, current))
        return self


def recommend(request: Assignment, *, now: float | None = None) -> dict:
    """Rank supplied snapshots without contacting Multica or reserving a run."""
    now = time.time() if now is None else now
    task, policy = request.task, request.policy
    ranked, excluded = [], []
    task_blockers = []
    if task.state not in ("new", "queued", "retry_authorized"):
        task_blockers.append("任务状态未知或不允许本次派发；结果不明的执行不能自动重派")
    if not task.inputs_verified:
        task_blockers.append("输入及其位置尚未完整核对")
    for candidate in request.candidates:
        reasons = list(task_blockers)
        if not candidate.enabled:
            reasons.append("成员已停用")
        if task.requested_agent_id and candidate.agent_id != task.requested_agent_id:
            reasons.append("不是任务指定的成员")
        if task.kind not in candidate.roles:
            reasons.append("成员未声明可担任此角色")
        if task.capability not in candidate.capabilities:
            reasons.append("成员未声明所需能力")
        if task.kind == "reviewer" and candidate.agent_id == task.executor_agent_id:
            reasons.append("验收成员必须与执行成员使用不同 agent ID")
        if any(machine != candidate.machine_id for machine in task.local_input_machine_ids):
            reasons.append("部分输入仅在其他电脑上，尚不可跨机读取")
        if candidate.online is not True:
            reasons.append("电脑离线或在线状态未知")
        if candidate.dispatchable is not True:
            reasons.append("客户端可执行状态未知或未通过检查")
        if candidate.observed_at is None or candidate.observed_at > now + 15 or now - candidate.observed_at > 120:
            reasons.append("状态观测缺失或已过期")
        for active, limit, label in (
            (candidate.agent_active, candidate.agent_limit, "成员"),
            (candidate.machine_active, candidate.machine_limit, "电脑"),
        ):
            if active is None or limit is None:
                reasons.append(f"{label}并发状态未知")
            elif active >= limit:
                reasons.append(f"{label}并发已满")
        if candidate.profile.max_difficulty is None:
            reasons.append("承接难度未声明")

        if not reasons:
            agent = {
                "provider": candidate.provider,
                "model": candidate.model,
                "preference": candidate.preference,
                "readiness": {"dispatchable": True},
                "routing_profile": candidate.profile.model_dump(),
            }
            score, explanation = evaluate(
                task.model_dump(),
                {"policy": policy.model_dump()},
                agent,
                {"accepted": candidate.accepted_recent, "failed": candidate.failed_recent},
                now,
            )
            if score is None:
                reasons.append(explanation)
            else:
                ranked.append(
                    {
                        "agent_id": candidate.agent_id,
                        "name": candidate.name,
                        "machine_id": candidate.machine_id,
                        "provider": candidate.provider,
                        "model": candidate.model,
                        "score": score,
                        "reason": explanation,
                        "observed_at": candidate.observed_at,
                        "valid_until": candidate.observed_at + 120,
                    }
                )
        if reasons:
            excluded.append({"agent_id": candidate.agent_id, "name": candidate.name, "reasons": reasons})
    ranked.sort(key=lambda item: (-item["score"], item["agent_id"]))
    excluded.sort(key=lambda item: item["agent_id"])
    return {
        "recommended_agent_id": ranked[0]["agent_id"] if ranked else None,
        "generated_at": now,
        "valid_until": ranked[0]["valid_until"] if ranked else None,
        "task_blockers": task_blockers,
        "ranked": ranked,
        "excluded": excluded,
        "note": (
            "只根据调用方提供的快照给出建议，不核验任务状态或重试授权，"
            "不读取 Multica、不预留名额或派发任务；派发前须再次核对运行时状态、"
            "代码可达性、实际订阅及验收会话的独立性。"
        ),
    }
