# 成员身份与分工配置

`recommend_live_team` 可以直接调用本地平台 CLI 获取代理、运行时和任务状态，结合团队资料推荐成员。配置 `AGENT_COOPERATION_PROFILES` 后，可选用 `AGENT_COOPERATION_MULTICA_COMMAND` 指定 CLI 路径、`AGENT_COOPERATION_MULTICA_PROFILE` 选择登录配置。数据采集开始时记录时间，采集时间过长时不会把旧状态当成新状态。无法直接访问平台时，仍可使用下文的快照方式。

团队配置使用 [示例文件](team-profiles.example.json) 的结构，每个成员有自己的 Multica 成员 ID、名称和可选的 Git / GitHub 身份；每个代理关联所属成员及 Multica 电脑和代理 ID。文件不存访问令牌、密码或订阅凭据。`daemon_id` 来自 `multica runtime list --output json`，`agent_id` 及代理所有者 ID 来自 `multica agent list --output json`。配置的所属成员必须与代理记录的所有者一致。

由成员填写能力和偏好：`roles`、`capabilities`、擅长任务 `preferred_for`、可承接难度 `max_difficulty`，以及 1–5 分的质量、速度、经济性（分数越高越符合这一项）。额度未知时保持 `unknown`；这些分数是团队自己的相对判断，不是系统推算的模型价格。可以逐步修改，不必一开始就了解所有模型。

在负责人使用的 PAL Agent Link MCP 配置中设置 `AGENT_COOPERATION_PROFILES` 为该 JSON 的绝对路径。负责人通过 Multica CLI 读取成员、运行时及任务列表后，调用 `recommend_configured_team` 获得候选、分配理由和归属；也可以把完整输出及开始收集时的 Unix 秒数 `observed_at` 放进本地 JSON，用 `recommend_configured_team_from_file` 读取。超过两分钟的快照不会用于推荐。当前 MCP 进程在部分客户端不能继承 Multica 的任务令牌，因此工具接收负责人传入的状态快照，无法独立验证来源或实际收集时间。推荐不会预留名额；完整多机并发和自动重试仍需验证。

用户、代理所有者、Git 提交作者与 GitHub 认证账号是不同身份。Git 交接检查可读出实际提交的作者和提交者，但这两个字段不是 GitHub 登录证明。当前配置与检查还没有接入 GitHub 凭据隔离和推送授权，不能把配置里的账号名称当成已经使用该账号执行了 GitHub 操作。

GitHub 身份接入的方向是成员各自通过浏览器授权，无需手动复制令牌。`agent_link/github_identity.py` 可使用独立 GitHub CLI 配置目录发起浏览器登录，从已登录账号读取用户名并推导符合 GitHub 当前规则的私有提交邮箱；早期账号的邮箱格式需要另行确认。`github_accounts.py` 记录成员与账号的绑定，并在使用前重新核对当前账号；`git_environment` 可为一次 Git 进程设置对应提交身份和 GitHub CLI 凭据助手。程序不存令牌。

这些功能尚未接入工作台登录、跨机凭据传递或 Multica 实际 Git 推送；Git 进程环境的测试仅覆盖本地提交。不能把登录网页或本地 Git 作者匹配当成代理已能以该账号完成远程操作。

本地创建测试小队可使用 `python -m agent_link.multica_team --name <小队名称> --leader-runtime <运行时ID> --worker-runtime <运行时ID> --profile <现有配置>`。该命令创建新的负责人、执行成员和小队；它不会覆盖已有同名成员。中途失败时保留已创建的成员并输出 ID，先检查现场再继续。模板使用 Multica 的任务、小队和阶段机制，不另建调度中心。
