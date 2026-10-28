# Multica 模型分配建议

`plan_agent_assignment` 是 PAL Agent Link 的只读 MCP 工具，供 Multica 负责人对当前任务的候选成员进行筛选和排序。它不调用模型、不读取 Multica、不创建任务、不预留并发名额，也不代表成员一定能接到任务。实际指派仍由 Multica 完成。

## 输入从哪里来

调用者应从当前 Multica 工作区的运行时、成员和任务获取最新信息，并由两名成员维护客户端、模型、角色、能力和质量／经济性偏好。不要用模型推测的值冒充运行状态或订阅余额。每个候选对应一个真实的 **Multica agent ID + 电脑 + 客户端及模型**；GLM 等模型必须由实际可用的客户端承载。

| 输入 | 含义 |
| --- | --- |
| `task.kind` / `capability` | 负责人、执行或独立验收角色，以及 `code`、`research`、`blender` 等团队约定的能力。 |
| `task.state` | `new`、`queued` 或 `retry_authorized` 才可获得建议；未知、执行中、结果不明或已完成均被阻止。工具不验证此声明或授权重试。 |
| `task.inputs_verified` | 调用者核对过全部依赖与其来源后设为 `true`；缺少来源时保持 `false`。 |
| `task.executor_agent_id` | 验收任务必须提供原执行成员的 ID，避免分配给同一成员。不同 ID 仍需配置为实际独立的执行会话。 |
| `task.requested_agent_id` | 显式指定成员时不会回退到其他成员。 |
| `task.local_input_machine_ids` | 全部尚未同步到 GitHub 的本地输入分别所在的电脑 ID。若这些输入位于不同电脑，没有单台电脑能读取全部输入时不推荐成员；同步后重新核对。 |
| `candidate.online` / `dispatchable` | 分别表示电脑在线与该客户端可接任务；未知时填 `null`。握手成功不等于账号、模型或额度可用。 |
| `candidate.observed_at` | 本次状态观测的 Unix 秒；超过 120 秒、缺失或明显处于未来的快照不参与排序。 |
| `agent_active` / `agent_limit`、`machine_active` / `machine_limit` | 已运行数量和允许并发。未知填 `null`，不会假定有空位；同一电脑的并发快照必须一致。 |
| `profile` | 成员声明的质量、速度、经济性（各 1–5）、最大难度、上下文容量、额度状态、暂停时间及擅长的能力。未声明最大难度时不会派发。 |
| `accepted_recent` / `failed_recent` | 调用者提供的最近最多 12 次结果；至少有 3 次时才对排序产生有限影响，不是权威模型评测。 |

额度状态支持 `unknown`、`available`、`limited` 和 `exhausted`；未知额度和未声明上下文会在选择原因中明确标出，已耗尽或暂停中的成员被排除。质量、速度与经济性为人工配置，不是平台推算的价格或保证。

## 调用示例

以下是构造请求的 Python 示例，仅展示数据形状。使用前必须用工作区的真实 agent ID、实际运行状态、并发数和已配置模型替换示例值；`observed_at` 只能填写实际完成观测的时间。

```python
import time

request = {
    "task": {
        "kind": "worker", "capability": "code", "difficulty": "standard",
        "state": "new", "inputs_verified": True, "local_input_machine_ids": [],
    },
    "policy": {"routing": "economy", "preferred_providers": ["qoder"]},
    "candidates": [{
        "agent_id": "replace-with-real-multica-agent-id",
        "name": "代码成员",
        "machine_id": "replace-with-real-machine-id",
        "provider": "qoder",
        "model": None,
        "roles": ["worker"],
        "capabilities": ["code"],
        "enabled": True,
        "online": True,
        "dispatchable": True,
        "observed_at": time.time(),
        "agent_active": 0,
        "agent_limit": 1,
        "machine_active": 0,
        "machine_limit": 2,
        "profile": {
            "quality": 4, "speed": 4, "economy": 4,
            "max_difficulty": "deep", "quota_state": "unknown",
            "preferred_for": ["code"],
        },
    }],
}
# MCP: plan_agent_assignment(request=request)
```

返回 `recommended_agent_id`、按分数排序的 `ranked`、带原因的 `excluded`、任务阻塞原因、生成时间和建议失效时间。没有符合条件的成员时，建议 ID 为 `null`。排序不涉及锁或预留：提交给 Multica 前仍需重新检查任务状态、成员是否在线、是否有并发空位、输入是否可读取以及账号是否可用，之后把分配理由记录到任务中。两个负责人并发使用本工具时，Multica 仍负责最终调度和并发控制。

当前工具不持久化全局配置、不自动同步 Multica runtime、不查询真实订阅费用或余额，也不保障不同 agent ID 一定对应不同客户端会话。这些仍是完整整合与实机验收的工作。
