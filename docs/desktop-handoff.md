# 桌面代理的本机任务交接原型

这个交接层让两个 MCP 进程通过同一台电脑上的 SQLite 文件协作：宿主代理使用 `host`，桌面会话使用 `desktop`。支持持续等待、定向领取、双向消息、进度回传和提交后接下一单；不会控制桌面窗口，也不会自动向 Multica 领取任务或回写状态。

先在该电脑为当前用户准备一个私有目录，例如 `C:\\Users\\用户名\\AgentCooperation` 或 `~/.agent-cooperation`。两个 MCP 配置必须在同一台电脑使用相同的数据库绝对路径；不要把文件放到共享网络目录。配置分别启动以下命令：

```text
python -m agent_link.desktop_handoff --db <数据库绝对路径> --role host
python -m agent_link.desktop_handoff --db <数据库绝对路径> --role desktop
```

使用当前项目 Python 环境的绝对路径替代 `python`。`host` 应由处理当前 Multica 任务的宿主代理使用；`desktop` 配置到支持 MCP 的桌面客户端。仅配置上述服务不会验证客户端登录或将任务交给它。

带 `internal-handoff` 入口的新版原生包也可直接运行交接服务，使用包最外层目录下的 `./pal-arena/pal-arena internal-handoff`（Mac）或 `.\pal-arena\pal-arena.exe internal-handoff`（Windows）替代上面的 `python -m agent_link.desktop_handoff`。不需要另装 Python，`--db`、`--role` 和 `--print-config` 用法相同；旧版原生包不包含这个入口。

在上面任一命令后加 `--print-config`，可输出该机器的 MCP JSON 配置。生成配置不会启动服务或创建数据库，也不会把令牌写入 JSON；实际启动时所需环境变量仍由对应宿主提供。

如果宿主 MCP 进程确实继承了当前 Multica 任务的 `MULTICA_SERVER_URL`、`MULTICA_TOKEN`、`MULTICA_TASK_ID`、`MULTICA_AGENT_ID` 和 `MULTICA_WORKSPACE_ID`，可在 host 命令后加 `--multica-task`。此模式在发布和每次续期前读取 Multica 的 issue 历史、核对运行状态及重试链；调用 `offer_task` 和 `renew_task` 时还需传入完整 `issue_id`。缺少环境变量时服务直接退出，不会退回手动校验。不同宿主是否传递这些变量、真实任务 token 的读取权限尚未验证。

1. 宿主检查任务及其完整重试链、准备好本机实际工作目录，调用 `offer_task`，填写根任务标识、当前任务 ID、绝对工作目录和工作说明。不要把 Multica token 或其他凭据放入说明。任务有效期为 60 秒；同时启用 Multica 校验和中心登记时宿主进程会自动核对并续期，否则需由宿主反复核实运行状态后调用 `renew_task`。
2. 桌面会话调用 `list_tasks`，再用返回的 `lineage` 调用 `claim_task`；保存结果中的 `handle`。它只能领取一次。如果领取响应丢失，先核对原会话和数据库记录，不得直接重派。
3. 桌面会话开始及继续工作前调用 `task_status`；若状态不是 `claimed`，停止工作。完成后调用 `submit_result`，提交说明和至少一条证据。结果只是候选交付，不自动代表验收通过。
4. 宿主通过 `inspect_task` 取得候选结果，自行检查文件和证据，再由 Multica 的正常工作流程回传。需要取消时调用 `cancel_task`，并确认桌面会话已停止；取消不会强制停止它。

代码任务提交候选结果后，宿主可调用 `verify_git_delivery`，传入完整的交付提交和基准提交哈希，检查当前工作区的 HEAD、基准是否为祖先、未提交及未跟踪文件，以及变更路径。该检查不会运行项目测试、读取远端分支、检查被 Git 忽略的文件或代替独立验收。

## 复用已经连接的 Pal

桌面会话已经连接 Pal 时，也可以通过 `agentlink` 工具使用同一套交接记录，无需再配置一个桌面交接连接器。更新代码后需重新连接当前会话中的 Pal，让它重新读取工具定义。

各操作都传入 `handoff_db`，值为宿主已创建的 SQLite 文件绝对路径：

- `action=handoff_list`：查看有效的待领交接。
- `action=handoff_claim`：传入 `lineage`，取得文档说明和领取凭据 `handle`。
- `action=handoff_status`：用同一个 `lineage` 和 `handle` 检查状态。
- `action=handoff_submit`：用同一组凭据提交 `summary` 和非空 `evidence` 列表。
- `action=handoff_next`：传入固定的 `consumer_id` 和本次领取的 `request_id`，最多等待 25 秒并原子领取下一项任务。
- `action=handoff_progress`：传入 `lineage`、`handle`、`message_id`、`message`，向宿主回报进度。
- `action=handoff_message` / `handoff_messages`：发送任务消息，或从 `after` 游标读取宿主消息。
- `action=handoff_wait`：等待消息或任务状态变化，最多 25 秒，返回 `next_after` 游标与 `timed_out`。
- `action=handoff_acknowledge`：处理宿主补充后，用消息的 `message_id` 和领取凭据确认；所有宿主消息确认后才能提交。
- `action=handoff_continue`：提交候选结果后等待下一单；额外传入 `consumer_id`、下一次领取的 `request_id` 和 `wait_seconds`。

这些操作直接复用原型的领取、到期、取消和提交规则，不调用模型 API，也不启动新的模型会话。文档可直接放在交接说明和结果摘要中，较大的文件通过证据路径交接。宿主仍使用上面的 `host` MCP 发布、续期和查看结果。

MCP 工具可用、任务已领取、结果已回传是三个不同状态，应分别检查。把条目写进 `.mcp.json` 不代表已有会话已加载它；单独启动 stdio 服务成功也不代表桌面连接器已经加载。默认工具由客户端主动读取和提交；已有 Claude 会话可选用下文的本机通知适配器。

## 让已有会话持续接单

更新代码并重新连接 MCP 后，桌面端可以使用 `cooperate` 提示词进入协作循环，或主动调用 `next_task`。这一步由客户端发起；服务端不能让一个没有调用等待工具的空闲会话自行开始推理。

1. 桌面会话调用 `next_task(consumer_id="claude-ui", request_id="claim-001", wait_seconds=25)`。同一个会话保持同一个 `consumer_id`；它会在 MCP 调用中等待，期间不用复制任务内容或操作鼠标。
2. 宿主用 `list_listeners` 查看当前有效的等待登记，再调用 `offer_task(..., recipient="claude-ui")`。任务继续遵守现有的 60 秒租约和独立工作目录规则，宿主必须验证任务仍有效并续期。
3. `next_task` 返回 `state=claimed`、`lineage`、领取凭据 `handle` 和完整说明。宿主的 `inspect_task` 同时能看到 `consumer_id` 和 `request_id`；这是领取回执。`consumer_id` 用于路由，不是新的账号或身份认证。
4. Claude 直接完成任务允许的前端工作，通过 `report_progress` 回报进展。宿主用 `post_message(kind="directive")` 补充要求；桌面通过 `wait_for_update` 接收消息，并检查取消或到期。测试和最终验收由宿主负责。
5. 桌面调用 `complete_and_wait`，提交当前结果、证据，并用新的 `request_id` 等待下一项任务。同一个 MCP 调用会返回 `next`；若其中已包含下一项任务，不要再调用一次领取。

原先通过 `claim_task` 手动领取的任务仍使用 `submit_result` 完成，再进入 `next_task`。旧工具和数据库记录保持兼容。

### 响应丢失、取消和超时

- 领取响应丢失时，用相同 `consumer_id` 和 `request_id` 恢复。返回 `recovered=true` 时先核对现有工作，不能重新执行。已提交、取消或过期的旧请求返回原状态，不会偷偷领取另一单。
- 同一消费者已有未处理的领取时，不能用新请求另领任务；即使旧租约过期，也要先检查和处理旧任务。定向任务不能被旧的无消费者领取接口领走。
- `complete_and_wait` 的重试必须保留提交内容、证据和下一次领取 ID。当前结果提交和下一次领取各自保留回执，连接中断不会自动重派。
- `idle` 表示本次等待没有任务；`timed_out=true` 表示没有新消息或状态变化。它们都不代表任务已完成，也不会续期。用户仍授权持续协作时，客户端可继续等待；用户要求停止时退出循环。
- 消息使用调用方提供的 `message_id` 去重。相同 ID、相同内容重试返回原回执；改变内容会被拒绝。每条消息最多 8 KiB，每个任务最多 200 条，分页游标保持稳定。
- 桌面收到宿主的每条补充后，先处理内容，再用 `acknowledge_message(lineage, handle, message_id)` 确认。读取消息本身不算确认；确认可用相同参数安全重试，宿主可在 `inspect_task` 的 `message_receipts` 查看状态。只要还有未确认的宿主消息，`submit_result` 和 `complete_and_wait` 都会拒绝提交；提交检查与宿主发消息使用数据库事务，避免并发时漏掉要求。升级后需要重启已有交接 MCP 进程，旧工具列表可使用下方的轻量客户端调用确认工具。
- 桌面读取和发送任务消息需要正确的领取凭据；取消或过期后不能再发送新消息、提交结果或恢复执行。等待登记只有短期有效性，不等于桌面应用永久在线。

`channel_capabilities` 返回协议版本、等待上限和能力声明。`application_wakeup=false` 表示服务端不支持唤醒空闲应用。当前已用两个独立的真实 MCP stdio 进程验证持续交接；这与真实 Claude 客户端已加载新版工具、进入等待并领取代码任务是不同的验证阶段。

## 通知已经运行的 Claude 会话

Claude Code 的[跨会话消息](https://code.claude.com/docs/en/cross-session-messaging#the-sessions-inbox-socket)支持本机 socket 或 Windows 命名管道。宿主加 `--claude-peer` 后，MCP 才会提供 `list_claude_sessions` 和 `notify_claude_task`。它们不会启动 Claude、恢复已退出的会话、操作窗口或改变接收方权限。

先用列表确认明确的 `sessionId` 和 `cwd`，发布带有接收者的任务，然后调用 `notify_claude_task(lineage, session_id, expected_cwd)`。调用者必须已获得用户向该会话发消息的授权。适配器核对进程启动时间、工作目录和管道对应的本机 peer key；不使用代表接收方子进程的 `CLAUDE_CODE_MESSAGING_TOKEN`，也不声明自己具有其他权限级别。接收方仍可保留或拒绝消息。

返回 `transport_written` 只说明已写入管道，`delivered=false` 明确表示还没有应用层回执。检查任务的 `consumer_id`、`request_id` 和进度消息，才能确认已领取；结果仍需独立验收。发送前先持久记录：如果会话或工作目录等预检明确失败、尚未尝试连接和写入，修正后可以重新通知；传输中断或进程崩溃留下 `uncertain` 时不会自动重发。已退出的会话不在列表中，任务也不会因此另开模型进程。

Claude 官方文档说明了本机消息入口，但未保证帧格式和注册文件的稳定性，因此此适配器默认关闭。当前版本支持 `peerProtocol=1`；客户端升级后应重新做实际领取验证。仅有适配器和传输夹具通过，不能宣称真实 Claude 已收到任务。

已有会话尚未刷新 MCP 工具定义时，可在源码环境中使用轻量的 MCP 传输客户端：

```text
python -B -X utf8 -m agent_link.handoff_client --db <数据库绝对路径> next_task "<JSON 参数>"
```

它只连接本机 stdio MCP 服务，不启动模型。支持 `next_task`、`task_status`、`report_progress`、`wait_for_update`、`acknowledge_message`、`complete_and_wait` 和 `submit_result`；参数也可用 `-` 从标准输入读取。原生包通过 `pal-arena internal-handoff-client` 提供同一入口。领取凭据只在当前会话和私有交接库使用，不要提交进 Git。

### 真实桌面验证

2026-10-29，在 Windows 上使用已有的 Claude Desktop 会话与 Codex，完成了这轮本机交接：

1. 宿主通过 MCP 发布“电脑与成员页”代码任务，通知明确选定的已有会话；桌面用 `next_task` 领取，宿主取得消费者与请求回执。
2. Claude 用 `report_progress` 回传进度，直接修改页面和样式，交回 Git 提交。宿主通过 `post_message` 补充验收要求，Claude 用 `wait_for_update` 读取并修正“执行中”被未知检测状态遮住的问题。
3. 最终候选通过 `complete_and_wait` 提交，提交为 [`ee9284d`](https://github.com/Jay1023CN/agent-cooperation/commit/ee9284d455fbb237319bc2665fc6feedf1ba784a)。相对基准只改动 `frontend/src/views/dashboard.js` 和 `frontend/src/styles.css`；宿主通过 `verify_git_delivery` 核对完整 SHA、祖先关系、变更范围和干净工作区。
4. Codex 独立完成回归：前端 13 项测试通过；在独立测试中心验证真实登录、机器与成员状态、详情弹窗、指南按钮和键盘操作，360px 深色页面无横向溢出。Python 全量 1438 项通过、18 项按环境跳过；旧 Windows 管道编码下的中文和 emoji 交接也已覆盖。

这轮确认的是同一台 Windows 上的真实桌面会话协作；跨电脑的完整 Arena 调度、依赖交接和订阅链路仍需另做实机联调。

客户端升级后，窗口和历史可能已经恢复，代码进程却还没有恢复。不能因为本机 inbox 列表为空就判断桌面已关闭。本次在保留原会话 ID 的情况下重启客户端、回到原会话，待连接器加载完成后恢复了消息管道，再以真实领取回执确认连接。

宿主停止续期后，任务到期便不能再领取、提交或自动续期；到期记录会保留供检查，不能视为可以重派。桌面会话本身不会被强制停止，必须通过 `task_status` 主动检查；续期依赖本机系统时钟。旧版本数据库里没有期限的未完成记录升级后视为已过期。

一个本地交接数据库不会把相同或嵌套的工作目录分配给不同任务，即使旧任务已经提交、取消或过期也不会自动放行。每个写任务应准备独立目录；这只是本地目录冲突检查，不能阻止使用其他数据库的客户端访问相同目录。

不加 `--multica-task` 时，原型不核验调用者提供的重试链。可选的[中心占用记录](coordination.md)可在同一个登记服务内阻止其他机器重复领取相同来源，但尚未经过真实多机验证。状态查询与本地领取之间仍存在时间差；工作区隔离、身份隔离和可靠的取消通知也未完成。只在受控的本地测试中使用。

`agent_link/multica_runs.py` 可用任务令牌读取上游 issue 的完整任务历史，沿 `attribution` 中的重试与重跑关系追溯，并拒绝缺失记录、循环、跨项目和非运行状态。当前用模拟 HTTP 验证；实际 token 作用域、任务身份来源和其他派生任务类型还需处理。
