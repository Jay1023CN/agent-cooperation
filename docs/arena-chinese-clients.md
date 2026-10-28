# 中国区与 GLM 客户端接入评估

本文只归纳公开官方文档、当前 PAL 源码和本次静态检查结果。官方文档能证明接口或命令存在，不能证明某个用户账号已获该功能、订阅包含额度，或客户端在目标电脑上可用。当前没有在用户的 Mac 或 Windows 电脑上完成这些客户端的真实账号联调。

| 客户端 | 本地执行与接口 | 当前 Arena 判断 | 主要限制 |
| --- | --- | --- | --- |
| Qoder | 官方 CLI 可用 `--acp` 通过标准输入输出提供 ACP；ACP 复用 CLI 登录状态。 | 已接入 ACP 工作区文件和终端回调，并用本地模拟 ACP 客户端验证。 | ACP agent 不等于已打开的 IDE 会话。CLI 登录、账号资格、模型访问及目标机器运行仍需实测。 |
| Qoder CN | 官方 CLI 参数文档列出 `--acp`。 | 使用同一 ACP 客户端能力，独立发现国内 CLI。 | 账号与国际版分开；当前订阅、CLI 登录和目标机器运行尚未实测。 |
| Z.ai ZCode CLI | 官方源码与使用文档提供本地 CLI、非交互 prompt、文件/命令工具、登录和结构化结果。 | **优先验证的 GLM 本地执行候选**；PAL 尚需单独的 subprocess 适配器。 | 非 ACP；headless 模式没有交互审批面。账号/方案必须由用户在真实客户端确认；本次未实机验证。 |
| WorkBuddy PC 本地助理 Open API | 官方 OAuth API 查询本地助理在线状态、发送消息、读取消息历史；支持 `permission_response` 消息类型。 | 仅供单任务、人工监督的已有助理跟进；不作为 Arena 可自动验收的 worker。 | 单一助理没有 task/session ID、最终状态、消息回复关联或正式完成事件；permission 请求的事件载荷格式也未完整公开。 |
| WorkBuddy 桌面 MCP | 桌面客户端主动启动本机 MCP 并调用工具。 | 已实现共享执行成员：领取、工作区操作和明确提交后，继续走 Git 快照与独立验收。 | 需要人在桌面会话发起领取；不能接收个人任务或担任独立验收。真实 WorkBuddy 客户端、订阅与目标设备尚未联调。 |
| WorkBuddy 云端任务 API | 官方 API 创建云端任务、查任务状态并通过 SSE/JSON-RPC ACP 交互。状态包括 `completed`、`failed` 等。 | 有独立云任务生命周期，技术上比 PC 本地 API 更适合自动状态追踪；**当前不启用**。 | 运行于云沙箱，不能直接访问 worker 的本地工作目录。公开资料没有说明它是否由现有订阅额度覆盖、是否消耗积分或产生额外费用，也未验证数据驻留/隐私条件。 |

## ZCode CLI：可依赖的命令行契约

ZCode 官方仓库说明其 CLI 支持交互 TUI 和非交互任务，CLI/runtime 源码也在仓库内。当前官方使用文档给出的无交互调用形式为：

```sh
zcode --prompt "任务说明" --cwd /absolute/path/to/project --mode edit --output-format json --no-color
```

参数按独立 argv 传递，启动器应禁用 shell 拼接。`--prompt` 必须带非空文本；`--cwd` 决定工作目录、项目配置、技能发现和会话归属。`--output-format` 支持 `text`、`json`、`stream-json`，显式设置后优先于旧的 `--json` 参数。

权限模式需要明确选择：

- `edit`：普通工作区文件编辑通常免审批，其他工具沿用 `build` 的权限判定。适合限定在任务工作区内的文件改动；它不是操作系统隔离，也不代表命令执行、外部副作用或插件工具都能运行。
- `build`：常规写入和有副作用的工具通常需要审批。普通 `--prompt` 没有交互审批客户端，因此这类调用可能被拒绝并以错误结束；要使用此模式，需要一个实际可用的审批界面/协议。
- `yolo`：跳过普通工具权限提示，官方文档只建议在已经隔离的无人值守环境使用。不要把它作为 Arena 的默认模式。
- `guarded`：当前源码支持，危险命令仍需逐次批准；旧版本可能不支持，且它不是无人值守的自动批准模式。

`json` 正常终止时，源码生成的摘要含 `sessionId`、`traceId`、可选 `turnId`、最终 `response`、`eventCount` 和执行 `projection`；有用量时另含 `usage`，多回合时可能含 `turnResponses`。`stream-json` 是逐行 NDJSON，先输出事件，末行是 `{"type":"result", ...}`，摘要字段与 JSON 形式基本相同。启动器应解析最后的 `type=result`，同时检查退出状态、工具事件/错误和工作区产物。模型最终文字本身不能证明文件或命令已正确执行。

参数或运行错误通常返回退出码 `1` 并向 stderr 写 `Error: ...`；成功入口通常返回 `0`。信号会触发 CLI abort/清理流程，但启动器仍必须记录原始退出码、signal 和超时，并由系统进程管理器终止整个子进程树。网络中断或父进程超时后的结果不确定时，不应自动重发写任务。

恢复时保存并显式传入 `--resume <sessionId>`，同时保持同一个 ZCode 数据目录和有效工作目录。并发任务不要依赖 `--continue`（它选择当前目录最近的会话）。JSON/NDJSON 布局以具体 CLI 版本为准；上游文档明确提示部分错误路径可能只有 stderr，也并非所有子命令都遵循 JSON 格式。

账号入口是 `zcode login [zai|bigmodel] [--no-browser]`，默认 provider 是 `zai`；它启动产品账号授权流程并将凭据写入 ZCode 凭据存储。官方 CLI 同时存在 Coding Plan API Key 路径，但本项目目标是用客户端账号订阅，因此不应把 API Key 路径当作登录替代。`login` 命令和 OAuth 代码只证明有账号登录流程，不证明用户当前套餐、地区、模型配额或客户端版本拥有预期权限；需在目标机器上登录并核对产品账号显示的模型/用量。

ZCode CLI 是一个本地子进程适配方向，不是 ACP agent。接入时应保留每台机器自己的已登录 OS 用户和 ZCode 数据目录；不能复制/抽取其凭据，也不能在切换到空数据目录后假定仍然登录。先核对 `zcode doctor --json`，再由用户账号完成一个无副作用提示任务及一次受控工作区编辑，最后单独核验命令工具和审批路径。官方源码要求 Node.js 24.14.0 与 pnpm 10.33.2 进行源码构建；各平台可分发包的运行要求以所用发行版说明为准。上述目标机器检查仍未完成。

## WorkBuddy 本地助理：监督边界

官方 API 对 `POST /openapi/v2/localassistant/message` 的成功响应会返回用户消息的 `message_id`；`GET /openapi/v2/localassistant/message?message_id=<id>` 会返回该消息之后的新消息。它可以做增量轮询，但 API 只暴露一个当前 PC 本地助理，不提供 session/task ID，也没有明确说明哪个 assistant 消息是某条 user 消息的最终回复。因此若保留人工监督桥接，最多允许一个 in-flight 任务，并要求发送端和人工用户在同一期间不再对该本地助理提交新任务；观察到其他消息时应中止自动关联。

可在任务提示里放入随机 nonce，并要求助理在候选结束时输出单个 JSON handoff，例如 `{"task_nonce":"...","state":"candidate_complete","summary":"..."}`。这只是模型生成的内容：它可能在工作未完成时提前输出，也可能被历史消息、并发任务或提示内容混淆，不能作为平台保证的完成状态。安全用法是将它当作“请人工检查”的信号；随后冻结该工作目录的写入、记录并人工检查 diff/提交/工具副作用，再要求独立 reviewer 基于实际产物给出验收结论。未完成这些步骤时任务应保持等待验收。

消息历史的 `content` 是数组，`msg_type` 来自下游 `metadata.msgType`，每条记录还有 `metadata` 对象；公开示例只展示普通文本，不定义可依赖的 final/complete 结构。API 允许用 `msg_type=permission_response` 发送 JSON 字符串，其中要求 `outcome`、原始权限请求的 `requestId` 和 `answers`。但文档没有给出消息历史中 pending permission 事件、`requestId` 与选项如何呈现的稳定 schema。因此不能假设只凭当前轮询响应就能可靠展示、审批或拒绝工具请求；实现完整审批链路前需要用户环境真实 payload 验证，并按每个 `requestId` 明确人工响应。普通 `text` 内容中的“我已批准”不能代替该字段。

WorkBuddy 云端任务端点需要额外 OAuth scope `user.task.readable`、`user.task.invokable`，会返回远程 ACP 链接、短期沙箱 token 和沙箱信息。云任务有明确状态及 ACP prompt 终态，文档称可通过 GET task 刷新过期 token；这只支持云端会话生命周期能力，不代表可挂载本机目录。其费用、订阅额度和数据处理条件在当前公开资料中不清楚，所以平台不得静默创建云任务，也不得把它列为用户已有订阅的本地 worker。

## 官方资料与验证范围

- Qoder ACP：<https://docs.qoder.com/cli/acp>。说明 `--acp`、CLI 登录状态复用，以及 ACP 客户端提供文件和终端能力。
- Qoder CN CLI 参数：<https://docs.qoder.cn/cli/cli-reference>。列出 `--acp` 标志。
- ZCode 官方源码仓库：<https://github.com/zai-org/ZCode>。Apache-2.0；本次查看了 README、CLI 参数/权限/自动化文档和 CLI 源码。
- ZCode CLI 参考：<https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/02-cli-reference.md>。
- ZCode 权限说明：<https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/05-permissions.md>。
- ZCode 非交互自动化：<https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/13-automation.md>。
- ZCode 账号/模型配置：<https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/03-config.md>；登录入口实现：<https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/packages/cli/src/login-command.ts>。
- WorkBuddy Open API（本地助理、云任务、ACP、产物）：<https://open.workbuddy.cn/docs/openapi>。
- WorkBuddy MCP 配置：<https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide>。

本评估是静态文档和源码核对，没有访问用户账号、提交模型任务、申请 API Key 或运行真实桌面客户端。测试/联调仍需在各实际电脑、用户订阅与工作区完成；任何“已支持账号额度”“已可运行”结论均须以那一步证据为准。
