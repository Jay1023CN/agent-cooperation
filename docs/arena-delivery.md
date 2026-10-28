# PAL Arena 交付包与安装

本页说明原生包、源码安装和升级方法。原生包是否可下载，以对应提交的 **Arena Native Delivery** GitHub Actions 运行结果和 Artifacts 为准；构建脚本存在不代表 Mac、Windows 包已经生成。当前交付工作仍需取得本次 CI 的原生包验证结果，再补入实际交付记录。真实订阅客户端和四台电脑的任务验收仍须在用户设备上完成。

## 选择正确的包

| 电脑 | 原生构建目标 | 包内归档名称 |
| --- | --- | --- |
| Apple Silicon Mac，包括 Mac mini | macOS arm64 | `pal-arena-darwin-arm64-提交号.tar.gz` |
| Intel / AMD Windows 电脑 | Windows x64 | `pal-arena-windows-amd64-提交号.zip` |

工作流在各自系统上构建，文件名和 `manifest.json` 使用实际系统与架构，不把 Linux 构建改名为 Mac 包。提交号是实际构建源码的 Git 提交前 12 位；PR 构建可能使用 GitHub 生成的合并提交。带 `local-qa` 或清单中 `source_dirty: true` 的包是未提交源码的本地测试包，不作为正式交付凭证。

原生包包含 Python 运行时、平台依赖和编译后的网页，**运行中心和 worker 不需要另装 Python 或 Node.js**。Git、Tailscale、Blender、各 agent 客户端及其订阅登录仍按机器分工单独安装。GLM 等模型需要由已有客户端提供，平台不会生成新的订阅权限。

这些包没有 Apple Developer ID / notarization 或 Windows Authenticode 签名。系统可能要求确认来源；不要为运行平台而关闭系统安全功能。SHA-256 用于核对下载完整性，不代替软件签名。

## 校验和解压

从对应 CI 运行的 Artifacts 下载本机版本；GitHub 外层 Artifact 解压后应包含一个平台归档和同名 `.sha256` 文件。

Mac 在该下载目录中先校验，再解压。第二条命令里的文件名换成实际下载的名称：

```sh
shasum -a 256 -c *.tar.gz.sha256
tar -xzf "实际下载的文件名.tar.gz"
```

Windows PowerShell：

```powershell
$Archive = '实际下载的文件名.zip'
$Expected = ((Get-Content -LiteralPath "$Archive.sha256").Trim() -split '\s+')[0]
$Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
if ($Actual -ne $Expected) { throw '归档校验不一致，请停止使用这个文件' }
Expand-Archive -LiteralPath $Archive -DestinationPath '.\PAL-Arena'
```

保留完整解压目录，放在当前用户可读取的固定位置。不要只复制其中的可执行文件。包的结构为：

```text
pal-arena-平台-架构-提交号/
  Start Server.command 或 Start Server.cmd
  Check Installation.command 或 Check Installation.cmd
  pal-arena/
    pal-arena 或 pal-arena.exe
    _internal/...
  docs/arena*.md
  source.tar.gz
  manifest.json
  SHA256SUMS
  READ-ME.txt
  LICENSE
```

`manifest.json` 记录实际系统、架构、Python 和主要依赖版本、源码提交、未提交修改标记及逐文件校验值。`SHA256SUMS` 同时覆盖清单本身；Mac 可在这个最外层目录执行 `shasum -a 256 -c SHA256SUMS`。正式包的 `source.tar.gz` 是同一提交的源码快照，不包含 Git 历史、运行数据或订阅登录缓存。

下文的原生命令都从这个**最外层目录**执行。路径包含中文或空格时，使用引号；PowerShell 执行带引号的完整程序路径时，在前面加 `&`。

## 首次启动 Mac mini 中心

Apple Silicon Mac mini 可使用 [v1.8.2 安装助手](install-mac-mini.command)。对应 Release 的 `Mac-mini-Setup-v1.8.2.zip` 解压后双击 `.command`：它下载并校验固定版本的原生包，安装到当前用户的 Applications 目录，创建登录自启服务，最后打开工作台。无需 Python 或 Node.js。安装助手本身只做过 Bash 语法检查；中心二进制需以对应原生构建的 macOS 启动及 MCP 检查结果为准，用户 Mac mini 上的安装还需实际运行确认。

安装助手遇到已有启动配置或占用中的 8765 端口会停止，不覆盖已有中心。数据库仍放在 `~/.pal-arena/server`。其他电脑访问前，需在 Mac mini 安装并登录 Tailscale，再从工作台开启共享。Intel Mac 不使用这个 ARM64 安装助手。

双击 `Start Server.command`，或执行：

```sh
./pal-arena/pal-arena serve
```

Windows 也能运行中心：

```powershell
.\pal-arena\pal-arena.exe serve
```

浏览器打开 `http://127.0.0.1:8765`。Owner 登录令牌位于 `~/.pal-arena/server/owner-token.txt`，Windows 对应当前用户目录下的 `.pal-arena\server\owner-token.txt`。在本机打开文件并复制到登录框；程序只打印文件位置。登录后为 partner 创建单独的成员令牌，为每台电脑创建单独的机器令牌。

中心默认只监听回环地址，项目数据在 `~/.pal-arena/server`。`--data` 可选择其他目录，`--quota-gb 5` 设置中心暂存预算。数据目录与程序目录分开，因此解压另一个程序版本不会清空项目。

没有公网 IP 时，四台电脑加入同一 Tailscale 网络，在 Mac mini 上执行：

```sh
tailscale serve --bg http://127.0.0.1:8765
```

用它返回的 HTTPS 地址访问网页，并把相同地址填入其他电脑的 worker 配置。Mac mini 上的 worker 可以使用 `http://127.0.0.1:8765`。不用开放路由器端口；平台不替你登录 Tailscale 或第三方客户端。

## 安装检查和工作机

双击 `Check Installation` 可做基本检查。要检查具体工作机配置，使用：

```sh
./pal-arena/pal-arena doctor --config "$HOME/.pal-arena/worker.json"
./pal-arena/pal-arena doctor --config "$HOME/.pal-arena/worker.json" --json --output "$HOME/arena-doctor.json"
```

Windows：

```powershell
.\pal-arena\pal-arena.exe doctor --config "$env:USERPROFILE\.pal-arena\worker.json"
```

`--no-network` 跳过中心和本地客户端的连接探测。检查不提交模型任务；已安装、协议可连接、账号已登录和模型额度可用分别报告，不能把找到可执行文件当成订阅验收。未安装 Blender、Tailscale 或未提供 worker 配置时，可能得到 `needs_attention`，应按该电脑的用途逐项判断。

在每台工作机创建本机配置目录，再生成模板：

```sh
mkdir -p "$HOME/.pal-arena"
./pal-arena/pal-arena worker-config --name 'Mac mini' --server 'http://127.0.0.1:8765' \
  --output "$HOME/.pal-arena/worker.json"
```

Windows PowerShell 示例：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.pal-arena" | Out-Null
.\pal-arena\pal-arena.exe worker-config --name '天选 6 Pro' --server 'https://你的设备地址.ts.net' `
  --output "$env:USERPROFILE\.pal-arena\worker.json"
```

`--output` 直接写入 UTF-8 文件，避免 Windows 管道的编码转换，并且拒绝覆盖已有配置。模板只是配置起点，必须按本机已安装并登录的客户端修改。删除不存在的成员，保留需要的负责人、执行者、独立验收者；详见 [运行说明](arena.md) 和 [模型分配与记忆](arena-routing-memory.md)。配置生成不会启动 agent。

把网页签发的机器令牌保存为当前用户目录中的 UTF-8 单行文本，例如 `~/.pal-arena/machine-token.txt`，不要放进代码仓库。Mac 设置：

```sh
chmod 600 "$HOME/.pal-arena/machine-token.txt"
```

使用文件读取令牌并运行 worker：

```sh
./pal-arena/pal-arena maintenance run-service --role worker \
  --config "$HOME/.pal-arena/worker.json" \
  --token-file "$HOME/.pal-arena/machine-token.txt" \
  --log-file "$HOME/.pal-arena/logs/worker.log"
```

Windows：

```powershell
.\pal-arena\pal-arena.exe maintenance run-service --role worker `
  --config "$env:USERPROFILE\.pal-arena\worker.json" `
  --token-file "$env:USERPROFILE\.pal-arena\machine-token.txt" `
  --log-file "$env:USERPROFILE\.pal-arena\logs\worker.log"
```

这个命令在前台运行，日志写入指定文件并轮转；停止前先查看活动任务。直接使用 `pal-arena worker --config ...` 时，机器令牌从 `ARENA_MACHINE_TOKEN` 环境变量读取，`worker` 子命令本身没有 `--token-file` 参数。工作机默认目录为 `~/.pal-arena/workspaces`，每台电脑独立保存。

## 登录时自动启动

先以前台方式完成中心、连接和客户端检查，再生成服务文件。Mac 中心示例：

```sh
./pal-arena/pal-arena maintenance service --platform macos --role server \
  --data "$HOME/.pal-arena/server" \
  --output "$HOME/.pal-arena/services-server"
```

工作机使用 `--role worker --config ... --token-file ...`；Windows 选择 `--platform windows`。生成工具返回 `.plist` 或任务 XML，以及配套 `.commands.txt`，**生成本身不会注册或启动服务**。按该文件执行安装命令，停止和卸载命令也在其中。

这是当前用户登录后的启动项。桌面 agent 需要该用户已登录桌面；它不保证电脑停在登录界面时就能操作桌面客户端。服务记录程序的绝对路径，升级到新目录后要更新服务定义。完整命令、日志位置和恢复步骤见 [维护文档](arena-maintenance.md)；该文档的原生命令从包内 `pal-arena/` 可执行文件目录执行。

## 升级和回滚

1. 暂停派发，检查并结束活动任务，记录结果未知的客户端会话或部署回执；停止工作机和中心的自动启动项，避免维护时自动拉起。
2. 用旧版本的 `maintenance backup` 创建独立备份。备份使用 SQLite 备份 API，同时保存对应产物；不要直接复制运行中的 `arena.db` 代替备份。备份包含登录凭据且未加密，留在当前用户受保护的位置。
3. 校验新包，将它解压到新目录，中心与全部工作机一起升级。保留旧程序、旧配置和备份，启动新中心时明确使用原来的 `--data` 路径。不要把程序包覆盖到数据目录或正在运行的程序目录。
4. 登录核对项目、成员、任务、产物和 doctor 结果，再更新服务路径并恢复工作机。当前中心要求 worker 协议版本 2，旧 worker 需要升级后才能重新注册和领取任务。只运行一个实际负责这些机器的中心实例。

本次运行令牌协议升级会使旧运行租约失效，原先运行中的任务进入执行中断，机器等待新 worker 重新连接。它们不会在升级后悄悄续跑。先检查中断任务、远端会话、GitHub 提交及本地回执，再手动决定重试。

旧二进制是否能读取新版本写入的数据库取决于架构兼容性。需要回退时，停止新中心，用升级前的备份恢复到一个**新的数据目录**，再用旧程序的 `serve --data 新目录` 启动。不要覆盖新数据库，也不要默认让旧程序直接打开已升级数据库。恢复会使旧会话和运行令牌失效，未结束任务转为执行中断；先检查外部副作用，再决定重试，见 [恢复说明](arena-maintenance.md)。

GitHub 工作分支和各机工作副本不在中心备份中，需单独保留。外部部署不会随数据库回滚自动撤销。

## 源码安装备用路径

没有对应的成功原生构建，或需要修改 Python 后端时，可以使用对应提交的源码。要求 Python 3.10+；代码任务还需要 Git。解压 `source.tar.gz` 到独立目录，或检出明确的 Git 提交，在源码根目录运行：

macOS：

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[platform]'
./.venv/bin/python -m arena doctor --no-network
./.venv/bin/python -m arena serve
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[platform]'
.\.venv\Scripts\python.exe -m arena doctor --no-network
.\.venv\Scripts\python.exe -m arena serve
```

首次安装需要访问 Python 包索引；源码包本身不是离线依赖安装包。每个版本使用自己的虚拟环境，数据仍放在 `~/.pal-arena/server`。正常源码运行使用仓库内已编译网页，无需 Node.js。源码安装的 worker 是 `python -m arena.worker`，维护命令是 `python -m arena.maintenance`，前面的 `python` 使用该版本的虚拟环境解释器。

修改界面时，使用独立的 `frontend/` 工程。安装 Node.js 22 的当前维护版本；先启动 8765 后端，再在另一个终端执行：

```sh
cd frontend
npm ci
npm run dev
```

开发地址是 `http://127.0.0.1:5173/static/`，`/api` 和 `/health` 代理到本机 8765。完成修改后执行 `npm run build`，生成 `arena/static/index.html` 和带哈希的 `arena/static/assets/`；这些生成文件需要与源文件一起提交。生产运行读取编译结果，不依赖 Vite 开发服务。

重新构建原生包需要带明确 Git 提交、无未提交修改的仓库，以及 `pyinstaller==6.22.3`。优先使用本仓库的原生交付工作流，它会编译网页、核对已提交资源、运行测试，再检查打包后的网页、MCP 和执行入口。工作流只上传 Artifact，不自动发布 Release。

## 连接已有 Multica / MCP 客户端

原生包可输出 Agent Link 的 stdio MCP 配置：

```sh
./pal-arena/pal-arena internal-agent-link --multica-config
```

Windows 使用 `.\pal-arena\pal-arena.exe internal-agent-link --multica-config`。输出中的绝对路径对应当前安装位置，移动程序后需重新生成。这一步只生成配置，不发送模型任务。Arena 内部任务自己的 MCP 入口由 worker 配置；原 PAL 的其他模型工具仍使用源码安装中的 `pal-mcp-server`，不应把它的 API Key 配置当成 Arena 工作机令牌。

## WorkBuddy 桌面 MCP

这条路径让已登录的 WorkBuddy 桌面客户端作为共享任务执行成员，不需要读取桌面客户端凭据、申请模型 API Key 或调用 WorkBuddy 云任务。桌面会话要由人启动并主动调用工具，不能自动唤醒关闭的客户端；配置中的模型与能力需要和客户端实际选择保持一致。

1. 在工作台「伙伴与电脑」为这个桌面执行端**新建专属电脑令牌**，不要复用普通 worker 的令牌。选择「WorkBuddy 桌面 · MCP」，启用唯一的执行成员，保持单槽，下载 `worker.json`。使用独立的工作目录，例如 `~/.pal-arena/workbuddy-workspaces`。
2. 将上一步令牌单独保存为本机用户目录里的文本文件，例如 `~/.pal-arena/workbuddy-token.txt`；不要把令牌写进仓库、MCP 配置或聊天。macOS 可对该文件执行 `chmod 600 ~/.pal-arena/workbuddy-token.txt`。Windows 请使用该用户的个人目录并限制文件访问权限。
3. 用该机器上实际存在的绝对路径生成 MCP 配置。原生包示例：

   ```sh
   ./pal-arena/pal-arena internal-desktop-mcp --config /绝对路径/worker.json \
     --token-file /绝对路径/workbuddy-token.txt --print-mcp-config
   ```

   Windows 使用 `.\pal-arena\pal-arena.exe internal-desktop-mcp` 和 Windows 绝对路径。源码安装使用对应虚拟环境的 `python -m arena.desktop_mcp`，参数相同。生成的配置只含文件路径，不含令牌内容；移动程序、配置或令牌文件后需重新生成。
4. 将输出的 `pal-arena-desktop` 条目合并到 WorkBuddy 的用户 MCP 配置 `~/.workbuddy/mcp.json`（或按官方说明配置到项目的 `.workbuddy/mcp.json`），保留已有 MCP 条目。重新加载客户端，并在一个专门的**共享工作会话**中要求它调用 `next_task`。如果没有可领取的共享任务，工具会返回空状态；准备较慢时可再次调用。
5. 任务返回后检查标题、需求版本和显示的工作目录。文件工具使用该目录；命令及修改执行配置需要在平台任务详情中逐次确认。客户端完成后必须调用 `submit_result`，附带本次返回的 handle、nonce、需求版本以及实际文件和验证说明。平台再捕获 Git 提交、收集 `arena-exports/` 并安排其他成员独立验收；提交本身不等于验收通过。

一个 MCP 进程一次只接一个任务，不会自动把它再交给第二个会话。如果领取结果丢失，`next_task` 会给出当前任务 ID 与尝试次数；先确保原桌面会话已停止，再用 `resume_task` 请求恢复，并在平台确认，避免两边同时写同一工作区。进程退出或失联后先检查平台和本机工作区，系统不会自动重发结果不明的写任务。

不要把私人对话内容放进这个共享会话；服务端不会将个人任务、个人记忆或私人聊天派给此执行端，但不能清除 WorkBuddy 自己已有的对话历史。桌面身份与普通 worker 在平台中是不同执行身份；若需要跨身份读取代码，使用 GitHub 同步工作分支。经确认的命令继承基础系统环境，不自动继承常见 API Key 等其他环境变量；命令以当前系统用户执行，工作目录校验不是操作系统沙箱。

已用模拟桌面客户端通过真实 MCP stdio 和本地平台 API 验证领取、文件提交与验收排队，并验证平台命令审批；尚未在你的 WorkBuddy、账号、Mac 或 Windows 上完成真实联调。官方客户端配置入口见 [WorkBuddy MCP 指南](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide)。
