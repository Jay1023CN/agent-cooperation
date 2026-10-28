"""Explainable selection over user-declared model profiles and observed outcomes."""

from __future__ import annotations

import json

LEVELS = {"routine": 0, "standard": 1, "deep": 2}
WEIGHTS = {"balanced": (3, 2, 2), "quality": (5, 1, 0), "economy": (1, 2, 5)}


def profile(value):
    return json.loads(value) if isinstance(value, str) else value or {}


def evaluate(task, project, agent, history, now):
    declared = profile(agent.get("routing_profile"))
    readiness = profile(agent.get("readiness"))
    if readiness.get("dispatchable") is False:
        return None, "本机接入检查未通过"
    if declared.get("quota_state") == "exhausted" or declared.get("paused_until", 0) > now:
        return None, "订阅额度暂停或手动冷却中"
    difficulty = task.get("difficulty") or "standard"
    if LEVELS[difficulty] > LEVELS[declared.get("max_difficulty", "deep")]:
        return None, "任务难度超过该成员配置的承接范围"
    needed, window = task.get("context_tokens", 0), declared.get("context_window")
    if needed and window and needed > window:
        return None, "声明的上下文容量不足"
    policy = project.get("policy") or {}
    mode = policy.get("routing", "balanced")
    quality, speed, economy = (float(declared.get(k, 3)) for k in ("quality", "speed", "economy"))
    qw, sw, ew = WEIGHTS.get(mode, WEIGHTS["balanced"])
    if difficulty == "deep" or task["kind"] in ("leader", "reviewer"):
        qw += 3
    elif difficulty == "routine":
        sw += 2
        ew += 1
    score = quality * qw + speed * sw + economy * ew - float(agent.get("preference", 10)) / 10
    reasons = [f'匹配 {task["kind"]}/{task["capability"]}', f"{difficulty} 任务；{mode} 策略"]
    preferred = policy.get("preferred_providers", [])
    if agent["provider"] in preferred:
        score += 10 / (preferred.index(agent["provider"]) + 1)
        reasons.append("命中项目优先客户端")
    if task["capability"] in declared.get("preferred_for", []):
        score += 5
        reasons.append("命中擅长任务声明")
    if declared.get("quota_state") == "limited":
        score -= 8
        reasons.append("已标记额度紧张，降低优先级")
    total = history.get("accepted", 0) + history.get("failed", 0)
    if total >= 3:
        score += 4 * (history.get("accepted", 0) - history.get("failed", 0)) / total
        reasons.append(f'最近 {total} 次：验收通过 {history.get("accepted", 0)}，执行失败 {history.get("failed", 0)}')
    label = agent.get("model") or "客户端默认模型"
    reasons.append(f'{agent["provider"]} / {label}；偏好评分由配置声明，未推算真实价格')
    if declared.get("quota_state", "unknown") == "unknown":
        reasons.append("剩余额度未知")
    if needed and not window:
        reasons.append("上下文容量未声明，运行时仍需确认")
    return score, "；".join(reasons)
