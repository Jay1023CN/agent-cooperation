# PAL Agent Link

让 MCP 客户端把任务交给本机编码代理或桌面会话，并读回进度、结果和错误。Python 3.10+，不包含固定用户名、盘符、端口或本机凭证。

## 连接方式

| 代理 | 接口 | 连接对象 |
| --- | --- | --- |
| Antigravity | 桌面本地 Connect RPC | 正在运行的桌面会话；不使用 CLI 回退 |
| OpenCode | HTTP API，区分 1.x / 2.x | 正在运行的服务，可与桌面客户端共享 |
| Qoder | 官方 ACP stdio | CLI 代理进程，不接管已经打开的 IDE 对话 |
| Qoder CN | 官方 ACP stdio | 独立的国内版 CLI、账号和会话 |
| WorkBuddy | 官方本地助理 Open API | 已在线的 PC 本地助理，需要 OAuth 凭证 |

## 安装

在本仓库目录中执行：

```sh
python -m venv .venv
# 激活虚拟环境后：
python -m pip install .
pal-agent-link --check
```

注册到支持 stdio 的 MCP 客户端：

```json
{
  "mcpServers": {
    "pal": {
      "command": "pal-mcp-server",
      "env": {"PYTHONUTF8": "1", "PAL_AGENT_ONLY": "1"}
    }
  }
}
```

客户端需要能在 PATH 中找到 `pal-mcp-server`。使用虚拟环境时，可在**用户自己的配置**中填写该环境的可执行文件路径；项目源码和示例不绑定某个人的目录。当前尚未发布到 PyPI。

## PAL 原生工具

使用首页的 `pal-mcp-server` 配置时，统一通过 `agentlink` 调用，参数 `action` 决定操作：

| action | 主要参数 |
| --- | --- |
| `status` | 无，查询所有代理 |
| `sessions` / `models` | `agent`，会话查询可加 `limit` |
| `create` | `agent`、`workspace`，可加 `title`、`agent_model` |
| `send` | `agent`、`session_id`、`prompt` |
| `read` / `wait` | `agent`、`session_id`，分别可加 `limit` / `seconds` |
| `permissions` | `agent`、`session_id` |
| `permission_reply` | `agent`、`permission_id`、`option_id`；省略 option_id 表示取消 |

`agent` 可取 `antigravity`、`opencode`、`qoder`、`qodercn`、`dsh`、`workbuddy`。`workspace` 必须是本机已存在的绝对目录。Antigravity 创建会话需要指定 `agent_model`；先用 `models` 查询。OpenCode 模型格式为 `provider/model`。WorkBuddy 仅使用固定的本地助理会话，其工作目录和模型在客户端中设置。

## 独立桥接入口

若只需这些适配器，可以把 MCP 配置中的 command 改为 `pal-agent-link`，无需设置 `PAL_AGENT_ONLY`。这个入口不加载 PAL 原有工具，会暴露以下工具；创建会话时模型参数名为 `model`：

- `agents_status`：查询连接状态。ACP 握手成功只说明协议可用，不代表账号或模型可执行任务。
- `plan_agent_assignment`：根据调用方提供的成员状态和已声明模型偏好，给出可解释的分配建议；只读、不预留名额、不自动派发。输入与限制见 [Multica 模型分配](multica-model-routing.md)。
- `agent_sessions` / `agent_models`：查询会话和模型。
- `agent_create`：指定 workspace，创建会话。Antigravity 需要明确 model；OpenCode 的 model 格式为 `provider/model`。
- `agent_send`：提交任务。返回 submitted 不代表完成。
- `agent_read` / `agent_wait`：读取输出、执行状态和错误。
- `agent_permissions` / `agent_permission_reply`：查看和回答 Qoder ACP 的权限请求；默认不自动批准。

后续消息沿用 session_id。ACP 会话在当前 MCP 进程中维护；MCP 重启后暂不自动恢复。Qoder 的模型选项在创建会话后返回。需要多个代理协作时，应明确各自负责的文件，避免同时覆盖同一个文件。

Qoder ACP 客户端提供任务工作区内的文本读写和终端；修改执行配置以及启动命令默认需要成员逐次确认。Arena 会在该轮结束、取消或连接关闭时停止其监督的终端进程并关闭该轮文件回调。POSIX 监督进程保留进程组身份，父进程意外退出时也尝试清理；若监督进程自身异常退出或结果状态丢失，平台会报告清理失败或结果未知。命令以当前系统用户运行，这不是操作系统沙箱；刻意脱离进程组的程序可能继续运行。Windows 的 `.cmd`/`.bat` 入口不接受，请使用原生可执行文件或 Node 与具体脚本。连接初始化失败或 agent 进程结束后，需重启 MCP 服务或创建新的适配器实例。

WorkBuddy 的 Arena 桌面 MCP 交接与下面的旧 Open API 是两条独立路径：桌面 MCP 使用已登录的客户端主动领取共享任务，见 [交付指南](arena-delivery.md#workbuddy-桌面-mcp)。实际账号与客户端仍需在目标电脑上验证。

## 自动发现与可选配置

默认通过用户标准目录、XDG 目录、PATH 和本机进程元数据发现接口。Antigravity 的令牌仅在内存中使用；OpenCode 从它自己的 service.json 读取本机服务信息，不读取模型供应商 auth.json。

所有代理均为可选。没安装某个客户端不会阻止其他客户端使用。特殊安装方式可以设置 `AGENT_LINK_CONFIG` 指向配置文件；独立入口也支持 `pal-agent-link --config config.local.json`。配置内容示例：

```json
{
  "opencode": {
    "url": "http://127.0.0.1:4096",
    "password_env": "OPENCODE_SERVER_PASSWORD"
  },
  "qoder": {"command": ["qodercli"]},
  "qodercn": {"command": ["qoderclicn"]},
  "dsh": {"profile": "cooperation"},
  "workbuddy": {"token_env": "WORKBUDDY_ACCESS_TOKEN"}
}
```

没有自定义需求时不要复制示例覆盖自动发现。command 是参数数组，不通过 shell 拼接。Windows 推荐原生 CLI，或者 `["node", "自己的 CLI 脚本路径"]`；自动发现也支持标准 npm 安装。

WorkBuddy 使用 `https://www.workbuddy.cn`。按[官方 OAuth 文档](https://open.workbuddy.cn/docs/openapi)注册第三方应用并完成用户授权，把 access_token 放入环境变量。所需 scope 为 `user.localassistant.readable` 和 `user.localassistant.invokable`。本项目不从桌面客户端提取登录凭证。token 续期由调用方管理，过期会返回明确错误。

WorkBuddy 固定使用 `session_id=local-assistant`。工作目录和模型在 WorkBuddy 内设置；该接口不能任意新建项目会话。消息返回不代表整个任务结束，适配器明确返回 response_received。

## 当前边界

- Antigravity 的内部桌面 RPC、OpenCode 2.x 的部分实验接口可能随客户端更新变化。
- 桌面服务需要由用户启动。此 MCP 不自动安装、登录或更改客户端权限。
- 桌面 HTTP 只允许本机回环地址，禁用代理和重定向。只有 Antigravity 本机服务允许自签名 TLS；WorkBuddy 保持标准 HTTPS 校验。
- 不自动重试任何写请求。超时后先查询状态，避免重复执行。
- Windows 是当前真实验证环境；Linux/macOS 使用通用发现逻辑，尚未做真实客户端验证。

## 开发

```sh
python -m pip install -e .
python -m pytest tests/test_agentlink_contracts.py tests/test_agentlink_tool.py -q
```

测试不使用真实账号。真实客户端的消息与文件编辑验收另行记录，连接失败不会作为验收通过。

需要在自己的环境做真实消息往返测试时，使用已登录的客户端及可丢弃的工作目录：

```sh
python scripts/agentlink_smoke.py --workspace /absolute/path/to/scratch --agents qoder qodercn
python scripts/agentlink_smoke.py --workspace /absolute/path/to/scratch --agents opencode --opencode-model provider/model
```

该测试会创建真实会话，但只要求固定文字回复，不修改项目文件。需要 ACP 权限测试时，运行 tests 中的本地测试即可，不会调用真实账号。

项目使用 Apache-2.0 许可证。新增契约测试已在 GitHub Actions 的 Windows、Linux、macOS 上通过，覆盖 Python 3.10 / 3.12。真实客户端验证情况见[验证记录](agent-link-release.md)。用户配置、登录态和真实测试会话不属于发布内容。

接口参考：[Qoder ACP](https://docs.qoder.com/cli/acp)、[Qoder CN 参数](https://docs.qoder.cn/cli/cli-reference)、[OpenCode Server](https://opencode.ai/docs/server/)、[WorkBuddy Open API](https://open.workbuddy.cn/docs/openapi)。OpenCode 2.x 的本次适配同时核对了客户端自带的 HTTP 客户端代码。

## DeepSeek Harness（DSH）

DSH 作为独立 harness 接入，使用 `dsh --profile <名称>` 提供的 ACP stdio，不指定模型供应商，也不接管桌面中的已有聊天。模型及凭证由 DSH 自己管理。配置方法与当前验证范围见 [DSH 接入说明](dsh-harness.md)。
