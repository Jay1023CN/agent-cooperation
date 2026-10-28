<p align="center">
  <img src="docs/images/hero.svg" alt="Agent Cooperation：你和伙伴，几台电脑，一张调度台。" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Jay1023CN/agent-cooperation/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Jay1023CN/agent-cooperation?style=flat-square&color=1f3a5f"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-1f3a5f?style=flat-square">
  <a href="LICENSE"><img alt="Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-1f3a5f?style=flat-square"></a>
  <img alt="不需要 API Key" src="https://img.shields.io/badge/API%20Key-%E4%B8%8D%E9%9C%80%E8%A6%81-2e7d4f?style=flat-square">
  <img alt="自托管" src="https://img.shields.io/badge/%E9%83%A8%E7%BD%B2-%E8%87%AA%E6%89%98%E7%AE%A1-0b7a8c?style=flat-square">
</p>

<p align="center">
  <b>简体中文</b> · <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="#它怎么工作">它怎么工作</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#能接入哪些客户端">客户端</a> ·
  <a href="#现在做到哪了">进度</a> ·
  <a href="#项目文档">文档</a>
</p>

---

你手上有 Claude Code、Codex、Qoder、Antigravity、WorkBuddy……分散在家里和身边的几台 Mac、Windows 上。每个都能干活，但它们互相不认识：你得自己拆任务、自己复制粘贴上下文、自己记住谁做到哪了、自己检查它说的“做完了”是不是真的做完了。

> **一句话**：你说目标，负责人 AI 拆成带验收条件的任务，分给在线电脑上的 AI 成员，各自在独立 Git 分支里做，再由另一位成员独立验收。
> 订阅用你已登录的，不要 API Key；自己部署，Tailscale 组网，不要公网 IP。
> **状态：早期。** 流程和界面已做好，用假 CLI 和协议夹具测过；多台真机 + 真实订阅的端到端联调还在进行（见[现在做到哪了](#现在做到哪了)）。

**Agent Cooperation 把这件事交给一张调度台。** 你和伙伴在团队频道里说目标，负责人把它拆成带验收条件的任务，分给在线电脑上合适的 AI 成员；每个任务在自己的 Git 分支里完成，再由**另一位成员**对照需求和证据独立验收，没通过就退回修订。全程用的是你们已经登录的客户端订阅，不需要任何模型 API Key。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/arena-preview-dark.png">
  <img alt="调度台界面：任务管线、等你授权提醒、团队频道和各台电脑的执行位" src="docs/images/arena-preview-light.png">
</picture>

<sub>示例数据截图。顶部管线条按状态统计全部任务；“等你授权”可以直接点允许或拒绝；右栏「机位」是每台电脑一条轨道，正在跑的任务一格一格摆在上面，下面是排队任务和它在等什么。示例里是四台，实际接几台都行。</sub>

## 它怎么工作

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/flow-dark.svg">
  <img alt="流程：你和伙伴提出需求，负责人拆分任务，分派到已接入的电脑并行执行，独立验收，没通过退回修订最多三次，通过后负责人汇总交付" src="docs/images/flow-light.svg" width="100%">
</picture>

1. **说目标。** 团队频道你和伙伴都能看到；想先自己理清楚，就私聊负责人，私聊不会改动共享需求。
2. **定需求清单。** 每一条都要能核对，清单有版本号。需求一改，旧版本的验收结果就作废，要重新核对。
3. **拆任务、派任务。** 负责人给每个任务写清能力、依赖、文件范围和验收条件。调度先看硬条件（在线、角色、能力、并发、代码在哪台机器），再按“平衡 / 质量优先 / 省额度”排序，**每次分派都写明理由**。
4. **各台电脑执行。** 执行机主动连接中心领取任务，在独立目录和 `arena/<task>/<attempt>` 分支里干活，交回提交 SHA、日志和产物。依赖任务读的是上一步确定的提交，而不是“大概是最新的代码”。
5. **独立验收。** 验收成员和执行成员不能是同一个；没有结构化结论和证据，就不算通过。不通过退回修订，自动修订最多三次，之后停下来等人处理。
6. **汇总交付。** 全部通过后负责人整合成果，交付必须对齐当前需求版本。合并、部署按项目策略来，比赛的最终提交仍由你们自己完成。

## 和“多开几个 AI 窗口”有什么不同

| | 手动多开 | Agent Cooperation |
| --- | --- | --- |
| 谁来拆任务 | 你 | 负责人，按需求清单拆，任务带验收条件 |
| 谁做完了 | 靠它自己说 | 看提交、日志、产物，再由另一位成员验收 |
| 断线或超时 | 不知道它到底执行没有 | 标记为“结果未知”，**绝不自动重发写操作**，等你确认现场 |
| 代码交接 | 复制粘贴 | 每个任务一条分支，按提交 SHA 接力 |
| 用量 | 各算各的 | 用已登录的订阅；额度查不到就显示“未知”，不假装知道 |
| 需求变了 | 大家各记一版 | 需求有版本，旧验收自动失效 |

## 适合谁，不适合谁

**适合**：手上有两台以上电脑、已经订了几个 AI 编程客户端、想让它们分工并行又不想自己当调度员的个人或小团队；想自托管、不想把代码和令牌交给第三方云。

**暂时不适合**：
- 只在一台电脑上并行多开 agent（单机场景有更轻的工具，我们的价值在跨电脑和验收）。
- 想要开箱即用的托管服务：这里需要你自己起中心、接电脑。
- 需要生产级稳定性保证：项目还早，真机联调没有完成。

还有几条是刻意的：

- **不假装在线。** 电脑在线、客户端装好了、订阅账号能用，是三个不同状态，界面分开显示。没接入的机器显示空状态，不拿示例数据充数。
- **记忆要人点头。** AI 提出的“记住这一点”先进待确认，成员批准后才会带进后续任务；每次运行都固定它用的那一版记忆。
- **私聊就是私聊。** 个人对话、它的任务、日志和产物只给本人看，不能改共享需求，也不能确认共享交付。

## 用起来顺手的地方

- **`Ctrl K` / `⌘ K` 一个框走遍全部**：页面、项目、任务、操作（新建任务、连接电脑、切换频道、换主题……）都能搜到，回车执行。
- **键盘就够了**：`N` 新建任务，`/` 直接给负责人发消息，`G` 再按 `C` `T` `R` `F` `M` 跳到对话、任务、验收、成果、电脑。
- **在原地处理卡点**：AI 请求授权时，顶部那条直接点「允许一次」或「拒绝」；失败的任务一键「检查后重试」。
- **页面在后台也不会错过**：开启桌面提醒后，有授权请求或任务失败会弹通知，标签页标题也带数量。
- **链接就是位置**：地址栏记着项目、页面和打开的任务（`#/<项目>/task/<任务>`），发给伙伴、刷新、收藏都会回到同一处；下次打开自动回到上次的项目。
- **装成应用**：浏览器地址栏里「安装」，就像一个独立 App，手机上也能「添加到主屏幕」。
- **命令一键复制**：弹窗里所有命令和 ID 旁边都有「复制」。
- **用你自己的 GitHub 身份提交**：项目设置里选一位成员，任务的提交和推送就以他的账号出现，见下文和 [说明](docs/arena.md#用成员自己的-github-账号提交和推送)。

## 快速开始

需要 Python 3.10+，并在每台电脑上先登录你打算用的客户端（Claude Code、Codex、Qoder 等）。

**1. 在常开的那台电脑上启动中心**（比如 Mac mini）：

```sh
git clone https://github.com/Jay1023CN/agent-cooperation.git
cd agent-cooperation
python -m pip install .
pal-arena serve --share
```

打开 `http://127.0.0.1:8765`，用 `~/.pal-arena/server/owner-token.txt` 里的令牌登录。中心只监听本机。

**2. 让其他电脑和伙伴连上。** 没有公网 IP、不会组网都没关系：每台电脑装上 Tailscale 并登录，`--share` 会自动给其他电脑一个 `https://…ts.net` 地址，网页「电脑与成员」顶部可以直接复制。第一次需要在 Tailscale 里点一次「启用 HTTPS」，命令会给你链接。一步一步的说明见 [组网指南](docs/network.md)。

**3. 接入执行电脑。** 在调度台的「电脑与成员」里点「连接电脑」，生成这台电脑专用的令牌，勾选本机已安装的客户端，下载 `worker.json`。然后在那台电脑上（Windows PowerShell 用 `$env:ARENA_MACHINE_TOKEN = '...'`）：

```sh
export ARENA_MACHINE_TOKEN='这台电脑的令牌'
pal-arena doctor --config worker.json
pal-arena-worker --config worker.json
```

`doctor` 只检查安装和本地连接，不会发模型请求，也不代表订阅额度没问题。第一次建议先派一个只读的小任务，确认结果、文件和验收都对得上，再加并发。

想让提交显示为你本人：在要推送代码的电脑上运行 `pal-arena github login --member <你的成员 ID>`（ID 在项目设置里可复制），浏览器授权一次即可。

只想先看界面：登录页点「不登录，先看示例」，或者访问 `/?demo=1`。完整的安装、校验、升级步骤见 [交付指南](docs/arena-delivery.md)。

## 能接入哪些客户端

| 客户端 | 接入方式 | 现状 |
| --- | --- | --- |
| Claude Code / Codex / Gemini | 官方 CLI，每个任务新建会话 | 可用；桌面版装了不等于 CLI 装了 |
| Qoder / Qoder CN | ACP 新建会话 | 可用；不接管 IDE 里已打开的聊天 |
| Antigravity | 本机桌面 RPC | 可用；可新建会话，也可接续指定的已有会话 |
| OpenCode | 本机 HTTP 服务 | 可用 |
| WorkBuddy | 桌面 MCP，由已登录的桌面会话主动领取任务 | 原型；真实客户端待实机验证 |
| GLM 等模型 | 由承载它的客户端选择 | 模型名本身不是可执行程序 |
| Blender | 派给装了 Blender 的电脑，产出 `.blend` 和导出文件 | 专用适配待联调 |

另外保留了上游 PAL 的 `clink` 工具和 [PAL Agent Link](docs/agent-link.md)：可以在任意 MCP 客户端里直接调用本机其他代理，也能为 [Multica](https://github.com/multica-ai/multica) 生成 MCP 配置。

## 现在做到哪了

- [x] 调度台网页：项目、需求版本、团队频道 / 私聊、任务看板、验收、成果、记忆、运行记录，浅色深色跟随系统
- [x] 中心服务：令牌登录、成员与电脑独立凭据、SQLite 存储、5 GB 暂存预算与清理、一致性备份
- [x] 调度：硬条件筛选 + 策略排序 + 分派理由；依赖等前置验收通过才启动
- [x] Git 交接：独立工作目录与分支、提交 SHA 证据、可选推送到 GitHub、发布器整合已验收提交
- [x] 独立验收与最多三次自动修订；结果未知时停止回写，不自动重复执行
- [x] 桌面交接原型与跨电脑任务占用登记
- [x] 用成员本人的 GitHub 账号提交和推送，推送前后双重核对作者
- [ ] 多台实机、真实订阅下的端到端联调
- [ ] WorkBuddy 与 Multica 的状态回写
- [ ] Blender 专用适配和可打开产物的自动检查

测试用临时 Git 仓库、假 CLI 和协议夹具跑，不消耗真实订阅；它们替代不了实机验收，上面没打勾的项就是还没在真实设备上验证过。

## 项目结构

```
arena/         调度台中心服务：API、调度、存储、执行端 worker、维护与发布
frontend/      调度台网页源码（Vite + 原生 JS），构建到 arena/static/
agent_link/    PAL Agent Link：本机客户端适配、桌面交接、跨电脑占用登记
clink/ tools/  上游 PAL MCP 的 CLI 桥接与多模型工具
docs/          说明文档
```

改界面只需要动 `frontend/`，见 [前端说明](frontend/README.md)。

## 项目文档

[调度台安装与运行](docs/arena.md) · [产品规格](docs/arena-product.md) · [交付与升级](docs/arena-delivery.md) · [维护与备份](docs/arena-maintenance.md) · [模型分配、记忆与代码交接](docs/arena-routing-memory.md) · [桌面交接](docs/desktop-handoff.md) · [跨电脑占用记录](docs/coordination.md) · [Agent Link](docs/agent-link.md) · [Multica 接入](docs/multica.md) · [整合方案](docs/multica-integration-plan.md) · [更新记录](CHANGELOG.md)

## 来源与许可

基于 [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server)，遵循 [Apache-2.0](LICENSE)。[Multica](https://github.com/multica-ai/multica) 以子模块引入，使用其自身的 [Multica License](https://github.com/multica-ai/multica/blob/8ffcc88/LICENSE)。
