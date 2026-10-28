# PAL Arena 安装与运行

PAL Arena 是供两名伙伴共用的自托管开发工作台：常开的 Mac mini 运行网页、数据库和产物存储，四台电脑各运行 worker，调用本机已经安装并登录的 agent。共享主对话交给负责人拆分任务，执行者按能力接单，不同成员独立验收，再由负责人汇总当前需求版本的交付。

平台使用已有客户端订阅，不要求模型供应商 API Key；第三方客户端可能有自己的登录、模型权限和额度限制。目标与协作范围见 [产品规格](arena-product.md)，安装、校验及升级按 [交付指南](arena-delivery.md)，备份和服务按 [维护文档](arena-maintenance.md)。

## 先启动中心，再接工作机

首选对应提交成功构建的原生包。Apple Silicon Mac 使用 macOS arm64 包，天选 Windows 电脑使用 x64 包。原生包带 Python 和网页，不需要另装 Python 或 Node.js；包是否已生成以 **Arena Native Delivery** CI 的 Artifact 为准，不把构建脚本视为已经交付的下载包。

校验、完整解压后，在包的最外层目录双击 `Start Server`，或运行：

```sh
./pal-arena/pal-arena serve
```

Windows 命令为 `.\pal-arena\pal-arena.exe serve`。打开 `http://127.0.0.1:8765`，用当前用户目录 `~/.pal-arena/server/owner-token.txt` 中的令牌登录。为 partner 签发独立成员令牌，为每台电脑签发独立机器令牌。默认中心数据在 `~/.pal-arena/server`，与程序目录分开。

Mac mini 没有公网 IP 时，四台电脑加入同一 Tailscale 网络，Mac mini 执行：

```sh
tailscale serve --bg http://127.0.0.1:8765
```

其他电脑的 worker 使用返回的 HTTPS 地址；中心仍只监听回环地址。Tailscale 与 agent 的账号登录要在本机完成。源代码安装可作为备用路径，完整命令见 [源码安装](arena-delivery.md#源码安装备用路径)。

每台机器生成并修改自己的 `worker.json`。原生入口是 `pal-arena worker-config` 和 `pal-arena worker`；令牌文件启动使用 `pal-arena maintenance run-service --role worker --token-file ...`，操作步骤见 [工作机配置](arena-delivery.md#安装检查和工作机)。先运行 `pal-arena doctor --config worker.json` 检查，再接入中心。检查不发送模型任务，也不会验证订阅剩余额度。

网页中的电脑、成员和日志来自真实注册与执行。尚未接入机器时显示空状态。示例预览使用明确标注的示例数据，不连接真实 agent。

## 配置实际能运行的成员

配置生成命令只提供模板。以下结构展示三个分工，并不代表电脑已安装或登录对应客户端：

```json
{
  "name": "Mac mini",
  "server": "http://127.0.0.1:8765",
  "slots": 2,
  "workspace_root": "~/.pal-arena/workspaces",
  "storage_limit_bytes": 5000000000,
  "agents": [
    {
      "key": "planner",
      "name": "项目负责人",
      "provider": "codex",
      "roles": ["leader"],
      "capabilities": ["planning", "code", "research"],
      "preference": 10
    },
    {
      "key": "qoder-worker",
      "name": "Qoder 执行",
      "provider": "qoder",
      "roles": ["worker"],
      "capabilities": ["code", "web", "docs", "research"],
      "preference": 5
    },
    {
      "key": "reviewer",
      "name": "Claude 验收",
      "provider": "claude",
      "roles": ["reviewer"],
      "capabilities": ["code", "web", "docs", "research"],
      "preference": 10
    }
  ]
}
```

`slots` 限制一台电脑同时执行的任务数量，每位成员同时执行一个任务。`model` 填对应客户端实际支持的模型 ID；CLI 可通过 `command` 参数数组指定完整程序路径。不要填写 shell 命令字符串。任务涉及测试、Blender 或其他工具时，仍受客户端自身权限设置约束；Qoder ACP 权限请求由成员在界面选择实际返回的选项。

调度先检查在线状态、角色、能力、接入情况、并发和代码所在机器，再按项目的均衡、质量优先或节省额度策略排序。`preference` 数值越小越优先，只是偏好之一。模型质量、速度、经济性、上下文容量和额度状态是配置声明，平台不把它们当成订阅账单或精确价格。详细配置与选择原因见 [模型分配、记忆与代码交接](arena-routing-memory.md)。

## 客户端接入边界

| 客户端 | Arena 使用的入口 | 当前边界 |
| --- | --- | --- |
| Codex、Claude、Gemini | 官方 CLI，新任务会话 | 桌面版安装不代表 CLI 已安装，也不自动接管 GUI 聊天 |
| Qoder、Qoder CN | ACP 新会话 | 需要实际 CLI 和账号；不支持接管已有 IDE 聊天 |
| Antigravity | 本机桌面 RPC | 需要运行中的桌面程序和有效模型；可显式指定已有会话及目录 |
| OpenCode | 本机 HTTP 服务 | 需要可连接的本地服务和客户端模型配置 |
| WorkBuddy | PAL 的受监督 MCP 桥接 | 当前接口不能可靠报告终态，**Arena 自动派发禁用** |
| GLM 等模型 | 使用该模型的客户端 | 模型名称本身不是可启动程序，也不授予访问权限 |
| Blender | 执行机上的 Blender 和能操作它的 agent | 任务使用 `blender` 能力，产物需在真实 Blender 中打开验收 |

WorkBuddy 的官方 OAuth 和在线本地助理仍可用于 [Agent Link](agent-link.md) 的人工跟进操作；不能把“已提交”视为完成，更不能作为 Arena 已通过自动验收的执行器。其他客户端的接口检查通过也只说明相应前置条件可检查，真实任务仍需要在用户订阅下验证。

Antigravity 的已有桌面会话需要明确配置对应关系：

```json
{
  "key": "desktop-scene",
  "name": "Antigravity 场景",
  "provider": "antigravity",
  "roles": ["worker"],
  "capabilities": ["blender", "code"],
  "mode": "existing",
  "session_id": "实际会话 ID",
  "workspace": "/absolute/path/to/existing/git-project",
  "model": "实际模型 ID",
  "adapter": {"model": "实际模型枚举"}
}
```

已有目录不切换分支，完成后捕获到独立交付分支。同一目录同时只执行一个任务，独立验收使用隔离目录。会话 ID 与桌面项目的对应关系由本机用户确认；另一台电脑不能仅凭这个 ID 获得相同工作目录。连接参数见 [Agent Link 配置](agent-link.md#自动发现与可选配置)。

## 代码、文件和独立验收

项目仓库使用不含凭据的 GitHub HTTPS 地址。每台执行机先通过本机 Git 凭据管理器等方式配置 HTTPS Git 读写；平台不把 Git 登录缓存传到中心。需要跨电脑接力时，开启项目的“同步工作分支到 GitHub”。

每次任务执行使用独立目录和 `arena/<task>/<attempt>` 分支，结果记录具体提交 SHA。依赖与验收读取该提交，成功验收后才继续后续工作；修改需求后，旧验收不能直接完成新需求。冲突、验证失败或推送失败会保留现场和原因，不覆盖别人的修改。

未开启远程同步时，本地提交只能由持有它的电脑读取。调度会按依赖、修订和待验收代码的位置约束机器；原电脑离线或没有合适的独立验收者时，任务保持排队。需要迁移到其他电脑，先同步相应提交并使用可访问的 GitHub 仓库，不能把共享网页误认为文件已复制到所有电脑。

共享项目的消息和任务供两名成员共同查看；个人会话及其任务、日志、产物只向该成员开放。个人会话不修改共享需求或确认共享交付。项目记忆与个人记忆分开，agent 只提出候选，由成员批准后进入后续上下文；每次任务保存实际使用的版本快照，详见 [记忆说明](arena-routing-memory.md#记忆如何进入任务)。

下载型成果放入任务目录的 `arena-exports/`。worker 上传常规文件，单文件上限 128MiB；中心暂存预算默认 5GB。新产物默认保护，解除保护后才能清理历史文件。中心计量包括数据库和产物，数据库写入不是文件系统硬配额；执行机另有工作目录预算，超过后暂停领取新任务。模型和 IDE 缓存不计入该预算，工作副本不会因中心清理而自动删除。

## 发布与恢复

自动合并或部署需要 GitHub 仓库、已确认推送的工作分支，以及在线发布成员的本地 `release_profiles`。发布成员的 `provider` 是 `release`、角色是 `publisher`，只执行配置好的验证与部署命令，不调用模型。项目选择对应的 `release_profile`；具体配置和首次空仓库处理见 [维护与发布](arena-maintenance.md#发布执行与首次空仓库)。

发布器整合当前需求版本已独立验收的提交，验证成功后才按项目策略普通推送或部署。主分支变化、冲突、验证失败都会停止；外部结果未知时不自动重复提交。比赛的最终提交仍由成员完成。

中心数据需通过 `maintenance backup` 生成一致备份；不要直接复制运行中的 `arena.db`。备份不包含执行机工作副本或客户端登录缓存，且其中的中心身份信息需要妥善保管。恢复写入新目录，旧运行令牌失效，未结束任务进入执行中断；检查 GitHub、客户端会话和本地回执后再决定重试。中心与工作机需要一起升级到当前的 worker 协议版本 2，本次运行令牌升级不会自动续跑旧任务。具体操作见 [备份与恢复](arena-maintenance.md) 和 [升级回滚](arena-delivery.md#升级和回滚)。

## 界面独立开发与验证

网页源代码位于 `frontend/`。它通过 HTTP API 连接中心，可独立运行 Vite 开发服务；`npm ci`、`npm run dev` 和 `npm run build` 的步骤见 [源码与前端开发](arena-delivery.md#源码安装备用路径)。生产服务读取 `arena/static/` 编译结果，普通用户不需要运行 Node.js。

自动测试使用临时 Git 仓库、假的 CLI 和协议夹具，不消耗真实模型订阅。原生包构建还检查无系统 Python 依赖的启动、静态资源、MCP 入口以及含中文和空格的程序路径。它们不能代替订阅登录、真实客户端操作、两机 GitHub 交接、四机并发、Blender 打开文件，以及断线恢复的实机验收。
