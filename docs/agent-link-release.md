# Agent Link 首版更新

本分支基于 BeehiveInnovations/pal-mcp-server 二次开发，保留上游 Apache-2.0 许可证。

- 新增 PAL 原生 `agentlink` 工具，统一查询连接、创建会话、提交任务、读取结果和处理 ACP 权限请求。
- 接入 Antigravity 桌面会话、OpenCode 服务、Qoder / Qoder CN ACP 和 WorkBuddy 官方本地助理 API。
- 增加 `PAL_AGENT_ONLY=1` 模式，无需配置模型供应商 API Key 即可启动代理工具。
- 自动发现标准安装目录和本机服务；源码不绑定个人目录，也不包含登录凭据。

## 本地验证

验证环境为 Windows。新增的 15 项契约和工具测试全部通过。通过 PAL 原生 MCP 工具完成 OpenCode、Qoder 的真实消息往返；独立桥接入口也完成了 Qoder CN 的真实消息往返。Antigravity 已在此前的桌面会话接入中完成消息及临时文件写入验证。

GitHub Actions 的 6 组契约测试已通过：Windows、Linux、macOS，分别使用 Python 3.10 和 3.12。[运行记录](https://github.com/Jay1023CN/pal-mcp-server/actions/runs/39898251199)。这些测试使用本地协议桩，不调用真实账号。

WorkBuddy 尚缺真实 OAuth 凭据和在线客户端，目前仅通过接口契约测试。Linux/macOS 尚未验证真实客户端。

### 路径与 shell 测试修复

首版曾有 12 项测试失败，现已修复：

- Windows 的系统路径策略会解析带根目录、但不带盘符的路径，保持系统目录及子目录的拦截。
- 主目录识别统一处理两种分隔符；目录扫描断言使用跨平台路径表示。
- 文件读取测试传入当前平台完整的绝对路径，继续验证系统目录拒绝访问。
- shell 测试自动查找 Git Bash，并使用它能够识别的路径。
- 新增目录同名前缀和 `..` 路径回归测试，确认正常目录仍可访问，进入系统目录的路径仍会被拒绝。

修复后，本机完整非 integration 测试为 **883 通过、4 跳过、0 失败**；另有 16 项 integration 测试未运行。相关路径与 shell 测试共 56 项，全部通过。Ruff、Black 和 isort 检查通过。这组回归测试也已加入六组跨平台 CI；新的远程运行结果以 Actions 为准。
