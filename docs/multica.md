# Multica + PAL Agent Link

> **可选的上游集成草案，不是当前主路径的部署指南。** PAL Arena 在本仓库内自带调度、执行端和验收流程，独立运行无需 Multica；从[交付指南](arena-delivery.md)开始。本文只适用于明确要把本机 Agent Link MCP 接入 Multica 的场景，以下步骤与 Arena 的任务队列、状态和桌面交接互不自动同步。

目标：在一个网页提交任务，连接多台电脑，每台电脑运行多个代理；由一名负责人拆分任务，可加一名评审代理，其余成员执行代码或建模任务。

完整的目标架构、各组件边界和未完成工作见[整合方案](multica-integration-plan.md)。本文是 PAL 作为 MCP 桥的接入草案，不代表已完成真实电脑联调。

Multica 参考版本也原生支持 Qoder、Qoder CN 和 Antigravity 的独立 CLI，可以直接绑定对应运行时，不一定需要经 PAL 再套一层宿主代理。它们与现有桌面会话不是同一个会话，实际订阅与 CLI 登录资格仍需确认。参考版本原生 Qoder 固定传入 `--yolo --acp`；PAL ACP 不使用该默认参数，但其权限请求仍由宿主调用者处理，不能假定一定会等待人工授权。部署前应明确选用的客户端和权限方式。

## 分工和边界

| 组件 | 职责 |
| --- | --- |
| Multica 服务端和网页 | 任务、小队、负责人、运行记录、评审、runtime 分配 |
| 每台电脑上的 Multica daemon | 在指定电脑启动受支持的编码 CLI，回传运行状态 |
| 每台电脑上的 PAL Agent Link | 通过 stdio MCP 调用该电脑的 Antigravity、OpenCode、Qoder 和 Qoder CN；WorkBuddy 本地助理 API 仅适用于人工监督 |
| 工作代理 CLI | 读取 Multica 任务，通过 PAL 调用本机目标代理，检查成果并回写任务 |

跨电脑通信走 Multica 的 daemon；PAL 的桌面 RPC/HTTP 仍只访问本机。不需要把桌面服务端口暴露到网络。PAL MCP 配置必须在目标电脑生成，其 Python 路径和配置文件路径属于该电脑。

PAL 启动 Qoder ACP 进程和其终端命令时会移除继承的 `MULTICA_*` 环境变量，避免把宿主任务身份直接交给受托客户端；因此该子进程不能依赖这些变量作为 Multica 任务身份。由宿主代理在授权范围内读取和回写 Multica，PAL 负责本机桥接。此处理不等于操作系统沙箱。

本次提供接入配置生成命令及下面的协作规范。尚未实现独立的 PAL runtime provider、PAL 会话逐条同步为 Multica run、跨电脑目录同步、自动文件锁或建模软件适配器。Arena 的 WorkBuddy 桌面 MCP 桥尚未连接 Multica。建模需要另行接入 Blender 等实际工具，不能仅靠接上 MCP 就认定可用。

负责人可用 PAL 的 `plan_agent_assignment` 工具对已知成员进行筛选和排序，具体见[模型分配说明](multica-model-routing.md)。它不连接 Multica 查询状态，也不会自动创建任务或预留执行名额；调用者需要提供真实、及时的状态快照与成员配置。

## 先跑通一台电脑

安装本仓库后，可先运行 `python -m agent_link --preflight`。它检查配置文件、MCP 在独立工作目录中的可导入性和常见命令是否在 PATH 中，不启动代理或提交任务。自定义配置可加 `--config <路径>`。客户端命令未找到不一定代表桌面版未安装；需要进一步检查连接时使用 `python -m agent_link --check`。

1. 安装并登录 Multica 支持且支持托管 MCP 的 CLI，例如 Claude Code 或 Codex。PAL 是 MCP 工具服务，不能单独代替 Multica 的执行 CLI。
2. 在这台电脑安装本仓库，先在要调用的目标代理里完成登录。桌面代理需要该用户的桌面程序在线。
3. 在 Multica 网页的 Runtimes 页面选择 Add a computer，按网页为当前工作区生成的命令连接该电脑。用 `multica daemon status` 检查连接。
4. 在 **PAL 所在的 Python 环境**生成配置：

   ```sh
   python -m agent_link --multica-config
   ```

   如果使用自定义配置：

   ```sh
   python -m agent_link --config /absolute/path/agent-link.json --multica-config
   ```

   命令只输出 JSON，不探测或启动目标代理，不把本地配置文件中的 token 写入 JSON。`AGENT_LINK_CONFIG` 也会转成明确的 `--config` 路径，避免 daemon 没继承终端环境时失效。

5. 创建一个绑定这台电脑 runtime 的 Multica 工作代理。在该代理的 MCP → Managed by Multica → Add MCP 中，按输出填写 STDIO `command`、`args` 和 `env`；JSON 编辑器若只编辑单个服务，填 `mcpServers.pal-agent-link` 内的对象。也可以把完整 JSON 保存为文件，使用 CLI 更新：

   ```sh
   multica agent update <agent-id> --mcp-config-file /absolute/path/pal-mcp.json
   ```

   更新会替换该代理私有 MCP 配置；已有其他服务时先合并 `mcpServers`，不要直接覆盖。

6. 在代理 Instructions 中加入下面的工作代理规范。发一个只读任务：列出 PAL MCP 工具，调用 `agents_status`，报告目标代理可用性，不提交编码任务。连接成功仍不代表账号、模型和文件写入均可用。
7. 再创建临时项目目录，提交一个写入 `hello.txt` 的验收任务。通过 `agent_create` → `agent_send` → `agent_read` / `agent_wait` 跑完，并由工作代理读取文件验证内容，把结果贴回 Multica。

## 扩到四台电脑

每台电脑重复以上安装和连接步骤，分别生成自己的 MCP 配置。每台先创建一个工作代理，跑通后再增加到 2–3 个。原生 CLI 已满足需求的成员可直接由 Multica 调用；需要现有桌面会话时才使用 PAL。

建议初始布局：电脑 A 运行负责人及评审，电脑 B/C 运行代码工作代理，电脑 D 运行工具或建模工作代理。实际分配按各机器性能和已安装工具调整。一个 Multica 小队只有一名 `leader_id`；第二名决策代理作为评审成员，由负责人安排独立评审任务。

Multica 小队不会自动广播任务。将父任务交给小队后，负责人必须明确创建并指派子任务。PAL 启动的内部会话不会自动成为独立的 Multica agent 或 run，因此需要把会话 ID、执行结果和阻塞信息回写到对应子任务。

跨电脑的项目各自 clone 同一个 Git 仓库；每个可写子任务使用独立 branch/worktree。先完成依赖任务再分派后续工作，合并时处理冲突。大型模型文件通过指定产物存储交付，并在任务中记录下载地址和版本；Multica 不会自动同步本地目录。

## 负责人 Instructions 模板

```text
你负责将用户目标变成可验收的任务，并协调小队成员。
先确认验收标准，查看成员能力、runtime 和在线状态，再创建并指派子任务。
根据真实且最新的任务和成员信息调用 plan_agent_assignment；填写全部本地输入来源和已声明模型偏好。
只把输出当作建议：派发前重新检查任务状态、成员在线及并发；未知结果不得自动重试。
如果无法取得足够信息，不要把未知状态填成可用；记录等待原因。
每个子任务包含：目标、输入仓库和版本、依赖、指定工作目录/独立分支、允许修改范围、验收命令、交付格式。
把互不依赖且文件范围独立的任务并行分派；有依赖的任务在前置成果可用后再分派。
不要假定小队成员会自动运行；使用当前 Multica 内置技能提供的任务操作。
父任务在执行期间保持进行中；收到成员结果后检查提交、测试和产物，再交评审代理独立验收。
有运行中的任务时不要重复提交相同工作。离线、权限请求、超时和失败应记录到对应任务，避免无限重试。
评审候选应与执行者使用不同 agent ID，并确认实际使用独立会话；完成结果汇总后交付评审。
由用户决定发布、部署或合并。
```

## 工作代理 Instructions 模板

```text
你只处理分配给自己的子任务。跨电脑任务交由 Multica 分配，PAL 只调用本机代理。
先检查目标工作目录和基准版本，明确允许修改的文件范围，使用该任务独立的 branch/worktree。
需要现有代理时，调用 agents_status；按任务选择一个目标，避免调用自己形成循环。
通过 agent_create 创建会话，workspace 使用当前任务实际的绝对目录；按目标代理要求选择模型。
通过 agent_send 提交一次任务，保存 agent 名称、session_id 和工作目录到 Multica 子任务。
submitted 仅代表接收，持续使用 agent_read/agent_wait 跟进。提交超时且结果不明时先查状态，不自动重发。
遇到 ACP 权限请求，读取 agent_permissions，仅在已有授权范围内回复，否则回报阻塞并等待用户处理。
收到完成状态后，自己检查 git diff、实际文件、测试结果及产物；不要只转述目标代理的成功消息。
回写：执行机器、目标代理、会话 ID、分支/commit、变更文件、验证命令和结果、产物位置、未完成事项。
PAL 进程退出后 ACP 会话不保证恢复，重启后先检查文件和任务记录，再决定如何继续。
```

## 验收清单

- 一台电脑完成只读探测和临时文件写入，产物经过工作代理复查。
- 两台电脑各完成一个互不依赖的子任务，负责人看到各自回传，再形成汇总。
- 四台电脑上线，每台达到预期并发；同一目录没有多个代理交叉写入。
- 注入一个权限阻塞、一个断线或失败任务，网页能呈现阻塞原因；不重复提交不确定状态的写操作。
- 评审代理可访问交付分支/产物，复查通过后父任务进入评审。

这些是实机验收步骤，不代表已在用户电脑上完成验证。部署需要明确服务端运行位置、电脑系统和目标代理。

## 上游参考

- [Multica 仓库](https://github.com/multica-ai/multica)
- [Runtimes](https://multica.ai/docs/daemon-runtimes)
- [代理与 MCP 配置](https://multica.ai/docs/agents-create)
- [Squads](https://multica.ai/docs/squads)
- [自托管](https://github.com/multica-ai/multica/blob/main/SELF_HOSTING.md)
