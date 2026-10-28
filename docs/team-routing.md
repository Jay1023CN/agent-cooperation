# 成员身份与分工配置

团队配置使用 [示例文件](team-profiles.example.json) 的结构，每个成员有自己的 ID、名称和可选的 Git / GitHub 身份；每个代理关联所属成员及 Multica 电脑和代理 ID。文件不存访问令牌、密码或订阅凭据。`daemon_id` 来自 `multica runtime list --output json`，`agent_id` 来自 `multica agent list --output json`。

由成员填写能力和偏好：`roles`、`capabilities`、擅长任务 `preferred_for`、可承接难度 `max_difficulty`，以及 1–5 分的质量、速度、经济性（分数越高越符合这一项）。额度未知时保持 `unknown`；这些分数是团队自己的相对判断，不是系统推算的模型价格。可以逐步修改，不必一开始就了解所有模型。

在负责人使用的 PAL Agent Link MCP 配置中设置 `AGENT_COOPERATION_PROFILES` 为该 JSON 的绝对路径。负责人通过 Multica CLI 读取成员、运行时及任务列表后，调用 `recommend_configured_team` 获得候选、分配理由和归属。当前 MCP 进程在部分客户端不能继承 Multica 的任务令牌，因此工具接收负责人传入的状态快照，不声称独立验证了快照来源或时效。推荐不会预留名额；完整多机并发和自动重试仍需验证。

用户、代理所有者、Git 提交作者与 GitHub 认证账号是不同身份。Git 交接检查可读出实际提交的作者和提交者，但这两个字段不是 GitHub 登录证明。当前配置与检查还没有接入 GitHub 凭据隔离和推送授权，不能把配置里的账号名称当成已经使用该账号执行了 GitHub 操作。

本地创建测试小队可使用 `python -m agent_link.multica_team --name <小队名称> --leader-runtime <运行时ID> --worker-runtime <运行时ID> --profile <现有配置>`。该命令创建新的负责人、执行成员和小队；它不会覆盖已有同名成员。中途失败时保留已创建的成员并输出 ID，先检查现场再继续。模板使用 Multica 的任务、小队和阶段机制，不另建调度中心。
