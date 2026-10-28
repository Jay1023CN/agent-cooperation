# Agent Cooperation · X（Twitter）发布方案

> 编写日期 2026-10-28。本文只是方案和草稿：不替你发布任何内容，也没有登录或操作任何社交账号。
> 产品事实只取自 `README.md`、`docs/arena-product.md`、`docs/network.md`、`docs/arena.md`。没有 star 数、用户数、评价可以引用，草稿里一律没写。

---

## 0. 先读这一节：三条红线和一个前置条件

1. **README「现在做到哪了」里没打勾的，一律不能写成可用**：多台实机 + 真实订阅下的端到端联调、WorkBuddy / Multica 状态回写、Blender 专用适配。WorkBuddy 只能说“原型”。
2. **所有现有截图是示例数据**（界面顶部有「示例数据，没有真实代理在运行」横条）。配图说明里必须写“示例数据”，不能让人以为是真实运行记录。
3. **“网页 GitHub 登录”另一个会话还在做**，合并发布之前不要提。已合入的“用成员本人的 GitHub 账号提交和推送”可以提。
4. **强烈建议的前置条件**：发布前至少在 2 台真实电脑上用真实订阅跑通一次“说目标 → 拆任务 → 分派 → 独立验收”，录下屏。调研里最有效的发布帖几乎都带真实运行的短视频或 GIF；我们现有素材全是示例数据，拿它当主图说服力最弱。跑不通也不要紧，三条 thread 都写了“状态很早期”的诚实版结尾，可以先发。

---

## 1. 调研：X 上传播广的开发者工具 / AI agent 发布帖

**方法与局限**：WebSearch 工具当时报模型错误不可用，我改用内置浏览器通过 Bing 找链接，再逐条打开 x.com 页面读取（不登录即可看到正文和浏览量）。数据读取于 2026-10-28，互动量是当时页面显示值。

- 互动数列顺序：浏览 / 回复 / 转发 / 点赞 / 收藏。页面显示的是浏览器本地时区（大概率北京时间），我在“时机”列里已换算成太平洋时间（PT）。
- 我只读到了每个串的**首条**，后续条数、是否 @ 了谁、是否同步 HN / Product Hunt / Reddit，在 X 页面上看不到，标“未核实”。没有替它们编。
- 页面文本里只有“GIF”“00:00”（视频）标记；没有标记不代表没有图片。

### 1.1 逐条记录

| # | 帖子 / 账号 | 日期 | 互动 | 开头第一句（≤15 字） | 结构与媒体 | 时机与配套 |
|---|---|---|---|---|---|---|
| 1 | [Cline Kanban](https://x.com/cline/status/2037182739695493399) · @cline | 2026-03-26 | 154.9万 / 231 / 597 / 3458 / 4702 | “Introducing Cline Kanban” | 单条。第一条里直接给出安装命令 `npm i -g cline`，一句话讲价值（任务在 worktree 跑、点开看 diff、卡片串成依赖链）。配 GIF。 | 23:59 北京 ≈ PT 上午 9 点；其余未核实 |
| 2 | [Claude Code 内置 worktree](https://x.com/bcherny/status/2025007393290272904) · @bcherny | 2026-02-21 | 130.7万 / 433 / 1094 / 1万 / 7099 | “Introducing: built-in git worktree support” | 单条。三句话：是什么、解决什么（并行不互相干扰）、哪里能用；末尾是 git 官方文档链接，不是产品页。无明显媒体标记。 | 账号本身自带流量；未核实配套 |
| 3 | [Browser Use + Jev](https://x.com/gregpr07/status/2100411066966749359) · @gregpr07 | 2026-09-17 | 326.8万 / 300 / 995 / 9129 / 9931 | “Breaking: Browser Use + Jev = Ultrafast” | 单条 + 视频（1 倍速真实录屏）。核心是一个具体数字：查航班 7 秒、花费 0.0039 美元；三行要点；最后一句“tiny open source… try it below”。 | 创始人个人账号；未核实 |
| 4 | [OpenDots（CopilotKit）](https://x.com/ataiiam/status/2105710796198322659) · @ataiiam | 2026-10-02 | 107.1万 / 243 / 741 / 6050 / 6886 | “Introducing OpenDots” | 单条，引用转发 OpenAI 自家产品发布帖，把自己定位为“自托管、可接任意 agent harness 的版本”；一个短列表列功能，附仓库链接，视频。**借势（newsjacking）**。 | 发在大厂发布后 2 天；其余未核实 |
| 5 | [Cursor：云端 agent 跑在自己的基础设施上](https://x.com/cursor_ai/status/2095257412781396114) · @cursor_ai | 2026-09-03 | 71.3万 / 252 / 370 / 3646 / 977 | “You can now run Cursor cloud agents…” | 单条，两句话：能力 + 为什么有用（访问内部服务、专用硬件）。视频。大厂官方号，流量不可比。 | — |
| 6 | [Cline CLI 2.0](https://x.com/cline/status/2022341254965772367) · @cline | 2026-02-14 | 55.2万 / 162 / 342 / 1879 / 1416 | “Introducing Cline CLI 2.0” | 单条，一句话定位 + 一行功能清单 + 限时免费模型钩子，视频。 | 未核实 |
| 7 | [Cumora 开源](https://x.com/dotey/status/2089404987587576191) · @dotey（宝玉，中文区 AI 博主）转介绍 yetone 的项目 | 2026-08-18 | 9.7万 / 62 / 57 / 400 / 488 | “yetone 开源了一个新项目 Cumora” | **长文单条**中文介绍：先说它像什么（“界面像 Slack，同事全是 AI”），再讲接入方式（用本机 Claude Code / Codex 订阅）、协调机制、当前状态（邀请制内测）和仓库。转引原作者的开源帖。 | 中文大 V 二次传播；**和我们最近的产品**，见 §1.3 |
| 8 | [Superset 的 YC 发布](https://x.com/ycombinator/status/2059348845902115220) · @ycombinator / @FlyaKiet | 2026-05-27 | 2.5万 / 13 / 19 / 194 / 75 | “Superset is an open-source IDE…” | YC 官方号转发创始人发布视频，句内带增长数字（周增 30%）。创始人原帖为“Introducing: Our users”（用户口述）+ 视频。 | **同步 YC Launches 页**（帖里链接可见） |
| 9 | [Superset for iPhone](https://x.com/ParadevAI/status/2102066246518976683) · @ParadevAI（原 Superset） | 2026-09-22 | 29.2万 / 86 / 95 / 480 / 173 | “Introducing Superset for iPhone.” | 单条两句：从手机远程控制电脑上跑的 agent；视频。 | — |
| 10 | [Vibe Kanban 停运但开源](https://x.com/tokengobbler/status/2042647208135123078) · @tokengobbler；[swyx 评论](https://x.com/swyx/status/2050753293601935777) | 2026-04-11 / 05-03 | 1.3万 / 22 / 5 / 114 / 21；9.7万 / 24 / 18 / 189 / 194 | “We’re shutting down bloop” | 不是发布帖，是**坦诚帖**：公司关了、项目继续开源、数据怎么导出。几乎无营销词。swyx 引用它并给出“3 万月活”和一句创始人的话，传播更广。 | 在 AI Engineer 大会现场；说明**第三方转述放大**很有效 |
| 11 | [Superset 的搬运帖 ×2](https://x.com/igus_ai/status/2059352027352764492) · @igus_ai、[另一条](https://x.com/mhdfaran/status/2030318538465141090) · @mhdfaran | 2026-03 / 05 | 1.8万 与 1.3万 | “BREAKING: A developer just built a terminal…” | 搬运号模板：红色警报开头、箭头列表、一句“大多数人是……我们是……”的对比、三个并行 agent 的具体例子、“你只负责协调”。互动数百。**可学结构，别学“BREAKING”语气。** | 项目方不一定参与 |
| 12 | [逛逛GitHub 介绍 Vibe Kanban](https://x.com/guangGitHub/status/2012412068499259827) | 2026-01-17 | 155 浏览 | 开头直接是项目名 | 中文长段落转述，**没有图和视频**，浏览仅 155。反面例子。 | — |
| 13 | [OpenClaw 的团队自述](https://x.com/steipete/status/2088473882357530979) · @steipete | 2026-08-15 | 11.2万 / 60 / 35 / 612 / 124 | “We moved the team over to build openclaw with openclaw” | 单条，一句“我们用自己的产品开发自己”+ 一句亮点。**吃自己的狗粮**。OpenClaw 的传播规模来自其他来源（项目被大量媒体与二次报道，我没有逐条核实它的数字，不在此引用）。 | — |
| 14 | [Conductor 融资与 YC 访谈](https://x.com/ycombinator/status/2052827442503631171) | 2026-05-09 | 7.9万 / 23 / 29 / 436 / 229 | “Conductor is a Mac app…” | 先一句话说它是什么（“一键创建隔离副本，让 Claude 或 Codex 去干，再审查合并”），再说融资和 Cloud 新功能；视频。 | 配 YC 访谈 |

### 1.2 共同规律

1. **第一句就是“它是什么 + 你能得到什么”，不是故事。** 高传播的首句几乎都是 “Introducing X” 或 “You can now …”，随后 1–2 句讲清机制。Cline Kanban、Superset iPhone、Cursor 都是这个形状。
2. **一个具体的、可以核对的细节胜过形容词。** Browser Use 的“7 秒 / 0.0039 美元”，Cline 的“`npm i -g cline`”，Boris 的“每个 agent 一个 worktree”。我们要用的是 `pal-arena serve --share`、`arena/<task>/<attempt>`、“最多三次修订”这类具体点。
3. **展示方式：单条 + 一段真实的短录屏/GIF 远多于长 thread。** 表中除搬运帖和长文转述外几乎都是单条；浏览量最高的 Browser Use（326.8 万）就是单条 + 真实录屏。thread 主要出现在“功能系列更新”（如 Cline Cloud Agents 用 “1/” 开头，首条 1.6 万浏览，远低于它的单条发布）。**所以 thread 的第一条必须能独立成帖，并且自带媒体。**
4. **可信度来源**：真实录屏（1 倍速也是卖点）、可复制的命令、开源许可证、创始人本人署名、YC / 大会这类第三方背书、第三方二次转述（宝玉转 Cumora、swyx 转 Vibe Kanban）。反面是没有任何媒体、只有形容词的转述（#12）。
5. **行动号召极轻**：命令直接放在帖里，或“try it below”，链接放在最后一条/回复。没看到长篇号召。
6. **借势和对比**：OpenDots 把自己放在大厂发布的“自托管版”位置，一晚上量级过百万。Superset 搬运帖用“大多数人 … 我们 …”对比。对比要具体，不要贬低对方。
7. **坦诚是一种内容。** Vibe Kanban 的停运帖互动很小，但被一位有影响力的人转述后传播更广。早期状态的诚实说明对开发者读者是加分项，不是减分项。

### 1.3 我们的位置：相近产品

读到的资料里和我们最接近的几个：

- **Cline Kanban / Conductor / Superset / Vibe Kanban**：看板或隔离工作区 + 并行 agent + worktree，主打**单台电脑上并行**（Conductor 帖里说得很清楚是 Mac 应用，另有 Cloud 版）。
- **Cumora**（yetone，开源）：“Slack 式的 AI 团队”，可以用本机 Claude Code / Codex 订阅（BYOA）。和我们“团队频道 + 本机订阅”的叙事**很接近**，不要把这两点当唯一卖点，会被一句“这不就是 Cumora 吗”打断。

我们能如实讲清、且上面几个帖子里没有看到的差异（来自 README）：

| 差异点 | 一句话 |
| --- | --- |
| 跨多台电脑 | 执行机主动连中心领任务，不是一台电脑上开多个窗口 |
| 独立验收 | 验收成员必须不是执行成员；没有结构化结论和证据不算通过 |
| 结果未知不重发 | 断线/超时标记“结果未知”，不自动重发写操作 |
| 分派带理由 | 每次分派写明为什么是这台、这个成员 |
| 需求有版本 | 需求一改，旧验收作废 |
| 组网 | `--share` 一条命令用 Tailscale 给其他电脑一个 https 地址，不需要公网 IP |

（对其他产品的描述只依据它们公开发布的帖子，评论区被问到时请用“据其发布介绍”这样的说法，不要替对方下结论。）

### 1.4 哪些做法不适合我们

- **“BREAKING / 🚨”搬运号腔调、夸大效率数字**（“10x”）：我们没有可引用的数据，也不想有。
- **借大厂发布借势**：OpenDots 的做法对“有现成模板仓库”的项目有效；我们没有对应事件，硬蹭会显得投机。
- **靠融资、YC、用户名单、月活背书**：我们都没有，不要暗示。
- **长篇中文转述不配图**：#12 反面例子。
- **单条“全功能清单”**：我们功能多（Ctrl K、授权、记忆、GitHub 身份…），首条只讲一件事。
- **把“示例数据”截图当成真实运行**：见红线。
- **HN / Product Hunt / Reddit 同步**：我没能在 X 页面上核实这些帖子有没有配套，所以不拿它们当依据。是否同步发 Show HN 是你的决定，见 §5。

---

## 2. 产品定位（写稿用的统一口径）

**一句话（中）**：把你分散在几台电脑上的 AI 代理（Claude Code、Codex、Qoder、Antigravity……）组成一支队伍：你说目标，负责人拆成带验收条件的任务，分给在线电脑上的成员做，另一位成员独立验收。

**一句话（英）**：Agent Cooperation turns the AI agents scattered across your machines into one team: you state a goal, a lead agent breaks it into tasks with acceptance criteria, members on your online computers do the work, and a different member reviews it.

**可以说**：自托管；Apache-2.0；用已登录的客户端订阅，不需要模型 API Key；Tailscale 组网不需要公网 IP；每个任务一个 Git 分支；独立验收；结果未知不自动重发；可选以成员本人 GitHub 账号提交；基于 PAL MCP Server。

**要如实写的状态**（README）：调度台、中心服务、调度、Git 交接、验收与修订、桌面交接原型、GitHub 身份提交**已做**；**未做**：多台实机真实订阅的端到端联调、WorkBuddy 与 Multica 状态回写、Blender 专用适配。测试用临时仓库、假 CLI、协议夹具。客户端表里 Claude Code / Codex / Gemini / Qoder / Antigravity / OpenCode 是“可用”，WorkBuddy 是“原型”，Blender 是“待联调”。

> 注意措辞：“可用”指适配已写好，不等于在多台真机上完整跑通过。thread 里统一用“已实现并用假 CLI 和夹具测过；多台实机端到端联调还在做”。

---

## 3. 三条发布 thread（中英各一版）

通用写法：

- **第一条自带媒体，且能单独成帖**；不放外链（外链放最后一条或第一条下的自我回复。这是 X 圈里常见的做法，我没有核实官方说法，当作经验）。
- 每条一个点。英文每条已控制在 280 字符内（代码块中的命令按实际字符数）。
- 最后一条一定是**状态说明 + 仓库链接**。
- `〔图：…〕` 是配图占位，具体拍什么见 §4。
- 仓库链接：https://github.com/Jay1023CN/agent-cooperation

### Thread A：“我不想再当 AI 的项目经理”（角度：调度台 + 一支队伍，面向所有开发者）

主发：英文。配图：第 1 条放 20–40 秒真实录屏（见 §4 A）。

**EN**

```
1/ I have Claude Code, Codex and Qoder spread across a Mac mini, a MacBook and a Windows PC.
Each is good. None knows the others exist.
So I became their project manager: splitting tasks, pasting context, checking whether "done" is true.
I built something to take that job. 〔video〕

2/ Agent Cooperation is a dispatch console.
You tell the team channel a goal. A lead AI turns it into tasks with acceptance criteria and assigns them to AI members on whichever of your computers are online. 〔image: arena-preview〕

3/ Each task runs on its own Git branch (arena/<task>/<attempt>) and hands back a commit SHA, logs and artifacts.
A dependent task reads the exact commit it depends on, not "whatever is latest".

4/ A different member then reviews the result against the requirements and the evidence.
No structured verdict and no evidence = not passed.
Rejected work goes back for revision, up to 3 times, then it stops and waits for you. 〔image: flow〕

5/ It runs on the client subscriptions you're already logged into. No model API keys.
Self-hosted. Tailscale for networking, no public IP. Apache-2.0, built on PAL MCP Server.

6/ Status, honestly: the console, scheduling, Git handoff and review loop are built and tested with fake CLIs and fixtures.
A full run on several real machines with real subscriptions is next. I'll post it either way.
Repo: https://github.com/Jay1023CN/agent-cooperation
```

**中文**

```
1/ 我手上有 Claude Code、Codex、Qoder，分散在 Mac mini、MacBook 和一台 Windows 上。
每个都能干活，但互相不认识。
于是我成了它们的项目经理：自己拆任务、自己复制粘贴上下文、自己核对“做完了”是不是真的做完了。
我做了个东西，把这份活接过去。〔视频〕

2/ Agent Cooperation 是一张调度台。
你在团队频道里说目标，负责人 AI 把它拆成带验收条件的任务，分给在线电脑上合适的 AI 成员。〔图：arena-preview（示例数据）〕

3/ 每个任务在自己的 Git 分支里跑（arena/<任务>/<第几次>），交回提交 SHA、日志和产物。
有依赖的任务读的是上一步确定的那个提交，不是“大概是最新的代码”。

4/ 然后由另一位成员对照需求和证据独立验收。
没有结构化结论和证据，就不算通过。
不通过退回修订，最多自动三次，之后停下来等你。〔图：flow〕

5/ 用的是你们已经登录的客户端订阅，不需要任何模型 API Key。
自托管，用 Tailscale 组网，不需要公网 IP。Apache-2.0，基于 PAL MCP Server。

6/ 说实话的状态：调度台、调度、Git 交接、验收这条链路已经做好，用假 CLI 和协议夹具测过。
多台真机 + 真实订阅的完整联调是我接下来要做的，跑完无论成败都会发出来。
仓库：https://github.com/Jay1023CN/agent-cooperation
```

### Thread B：“闲置电脑 + 现成订阅 = 一支队伍”（角度：自托管、不要 API Key、不要公网 IP）

主发：英文，发在 A 之后第 2–3 天，面向 self-hosting / homelab / 订阅党。

**EN**

```
1/ You probably already pay for a few AI coding subscriptions and own more than one computer.
What if they worked as one team, with no API keys, no public IP and no router config?
Here's the 3-step setup of Agent Cooperation. 〔GIF: terminal〕

2/ Step 1, on the always-on machine (say, a Mac mini):
pip install . && pal-arena serve --share
The hub listens on localhost only.

3/ Step 2: --share asks Tailscale to give your other computers an https://….ts.net address. The web UI shows it with a copy button.
First time only: click "Enable HTTPS" once in Tailscale; the command gives you the link. 〔image: machines page — to be captured〕

4/ Step 3: in the console click "Connect a computer". It makes a per-machine token and a worker.json. On that computer:
pal-arena doctor --config worker.json
pal-arena-worker --config worker.json

5/ Be careful what "doctor" proves: it checks install and local connectivity. It sends no model requests and says nothing about your subscription quota. Run a small read-only task first, then raise concurrency.

6/ Clients with adapters: Claude Code / Codex / Gemini CLI, Qoder, Antigravity, OpenCode. WorkBuddy is a prototype and Blender is not wired up yet.
Early stage; multi-machine end-to-end runs are still in progress.
https://github.com/Jay1023CN/agent-cooperation
```

**中文**

```
1/ 你大概率已经订了好几个 AI 编程工具，手上也不止一台电脑。
如果它们能组成一支队伍，而且不要 API Key、不要公网 IP、不用改路由器呢？
Agent Cooperation 的三步接入。〔GIF：终端〕

2/ 第一步，在常开的那台电脑上（比如 Mac mini）：
python -m pip install . && pal-arena serve --share
中心只监听本机。

3/ 第二步：--share 会通过 Tailscale 给其他电脑一个 https://….ts.net 地址，网页里有复制按钮。
只有第一次需要在 Tailscale 里点一次“启用 HTTPS”，命令会给你链接。〔图：电脑与成员页（待截图）〕

4/ 第三步：调度台「电脑与成员 → 连接电脑」，生成这台电脑专用的令牌和 worker.json。到那台电脑上：
pal-arena doctor --config worker.json
pal-arena-worker --config worker.json

5/ 先说清 doctor 能证明什么：它只检查安装和本地连接，不发模型请求，也不代表订阅额度没问题。建议先派一个只读小任务，再加并发。

6/ 已有适配的客户端：Claude Code / Codex / Gemini CLI、Qoder、Antigravity、OpenCode。WorkBuddy 目前是原型，Blender 专用适配还没接。
项目很早期，多台真机端到端联调还在做。
https://github.com/Jay1023CN/agent-cooperation
```

### Thread C：“AI 说‘做完了’，不能直接信”（角度：可靠性与验收，最有观点、最容易引发讨论）

主发：英文，发在 B 之后。第一条是观点，不放链接。

**EN**

```
1/ "Done" from an AI agent is a claim, not a fact.
In Agent Cooperation every claim has to survive a different agent checking it. Here's how that works, and what we deliberately don't do. 〔image: flow〕

2/ The reviewer can't be the member who did the work.
It checks the commit, logs and artifacts against the task's acceptance criteria. No structured verdict and no evidence means the task is not passed.

3/ Requirements are versioned. When the checklist changes, earlier acceptance results are void and have to be re-checked against the new version.

4/ Disconnects and timeouts are marked "result unknown". We never auto-resend a write operation. A human looks at what actually happened first.

5/ Every assignment records why: online, role, capability, concurrency, which machine holds the code, then the ranking policy (balanced / quality-first / save quota).

6/ AI-proposed memories wait for a person to approve them. Each run pins the exact memory version it used.

7/ This is built and tested with fakes and fixtures, not yet proven across many real machines. Feedback on the design is welcome.
https://github.com/Jay1023CN/agent-cooperation
```

**中文**

```
1/ AI 说“做完了”，是一句声明，不是事实。
在 Agent Cooperation 里，每一句声明都要经过另一个 agent 检查。下面是怎么做的，以及我们刻意不做什么。〔图：flow〕

2/ 验收的人不能是干活的那一位。
它对照任务的验收条件，核对提交、日志和产物。没有结构化结论和证据，就不算通过。

3/ 需求有版本号。清单一改，之前的验收结果作废，要对照新版本重新核对。

4/ 断线和超时会被标成“结果未知”。我们绝不自动重发写操作，先由人确认现场到底发生了什么。

5/ 每次分派都写明理由：在线、角色、能力、并发、代码在哪台机器，然后才是排序策略（平衡 / 质量优先 / 省额度）。

6/ AI 提出的“记住这一点”要等成员点头才生效。每次运行都固定它用的那一版记忆。

7/ 这套是用假 CLI 和协议夹具测过的，还没在很多台真机上证明过。欢迎对设计提意见。
https://github.com/Jay1023CN/agent-cooperation
```

---

## 4. 配图 / GIF / 视频：具体拍什么

### 4.0 现有素材够不够

| 文件 | 能用在 | 够不够 / 注意 |
| --- | --- | --- |
| `docs/images/arena-preview-light.png` / `-dark.png`（2880×1800，各约 0.5 MB） | A-2、短帖 S1 | 够用。比例 1.6:1，时间线里会被轻微裁切，上传时先看预览；**必须标“示例数据”**。 |
| `docs/images/flow-light.svg` / `flow-dark.svg` | A-4、C-1 | 内容是流程图，适合。**X 不支持 SVG，要导出 PNG（建议 1600×900，浅色）**。 |
| `docs/images/hero.svg` | 个人主页置顶图/备用首图 | 同样要导出 PNG；横幅比例较宽，在时间线里字会很小，不适合做首条主图。 |
| `agent-cooperation-poster.png`、`agent-cooperation-workflow.png`（各约 1.5–1.9 MB） | 备用 | 我没有打开确认内容，发布前请自己看一眼是否与现在的界面一致、有无过时文字。 |

**缺的（按重要性）**：1）真实运行录屏（最关键）；2）`Ctrl K` 命令面板 GIF；3）“等你授权”就地点允许的 GIF；4）`--share` 终端 GIF；5）验收被退回的界面截图；6）电脑与成员页截图（含 `ts.net` 地址和复制按钮）；7）一张 16:9 的对比卡片。

### 4.1 每条 thread 拍什么

**Thread A 首条视频（20–40 秒，无需配音，加字幕）**
1. 0–5 秒：团队频道里输入一句真实目标（如“做一个能在手机上打开的城市路线页，带 3 个路线示例”，换成你真实要做的东西）。
2. 5–15 秒：负责人回复需求清单和任务列表；右栏“机位”上两到三台电脑的轨道各出现一格任务。
3. 15–25 秒：顶部“等你授权”条出现，点“允许一次”。
4. 25–40 秒：验收成员接手，打出“通过”或“退回修订”；结尾停在任务看板。
要求：真实运行，1 倍速；如果加速，右下角写“8× 加速”；窗口里不要出现令牌、邮箱、私有仓库名。

**Thread B 首条 GIF（≤15 秒）**：终端里 `pal-arena serve --share` 的输出，到打印出 `https://….ts.net` 地址为止；然后切到网页“电脑与成员”顶部的地址和复制按钮。把主机名换成通用名或打码。

**Thread C 首条**：用导出的 `flow-light.png`；如果要更有冲击力，再做一张对比图：左边“手动多开：它说做完了”，右边“Agent Cooperation：提交 SHA + 日志 + 另一位成员的结论”，数据全部来自 README 的对比表，不要加没有的数字。

### 4.2 短帖配图

见 §5。

---

## 5. 可单发的短帖（5 条，中英）

**S1 · 机位轨道（配 `arena-preview-light.png` 的右栏裁剪图，图注“示例数据”）**
- EN: `Every computer gets its own lane. Running tasks sit on it as blocks; queued tasks wait below with the reason they're waiting. Four lanes in the demo, connect as many as you have. (Demo data.)`
- 中: `每台电脑一条轨道，在跑的任务一格一格摆在上面，排队的任务写明在等什么。示例里是四台，实际接几台都行。（示例数据）`

**S2 · Ctrl K（配 5–8 秒 GIF）**
- EN: `Ctrl K / ⌘K searches pages, projects, tasks and actions (new task, connect a computer, switch theme). N for a new task, / to message the lead, G then C/T/R/F/M to jump around.`
- 中: `Ctrl K / ⌘K 一个框搜页面、项目、任务和操作（新建任务、连接电脑、换主题）。N 新建任务，/ 直接给负责人发消息，G 再按 C/T/R/F/M 跳页。`

**S3 · 就地授权（配 GIF：点“允许一次”）**
- EN: `When an agent asks for permission, the bar at the top of the console has Allow once / Deny right there. A failed task has a one-click "check and retry".`
- 中: `AI 请求授权时，调度台顶部那条直接点“允许一次”或“拒绝”。失败的任务一键“检查后重试”。`

**S4 · 观点帖（纯文字，最适合引发讨论）**
- EN: `A rule we chose on purpose: if a worker disconnects mid-task, the result is "unknown". We never auto-resend a write operation. Retrying blindly is how you get two half-finished branches and a bad afternoon.`
- 中: `我们刻意定的一条规矩：执行中断线，结果记为“未知”，绝不自动重发写操作。盲目重试，换来的是两条半成品分支和一个糟糕的下午。`

**S5 · 提交身份（图：GitHub 上一条以成员本人账号出现的提交截图，需你实拍）**
- EN: `Task commits can show up under a member's own GitHub account instead of a bot. Log in once with pal-arena github login --member <id>; authors are checked before and after the push.`
- 中: `任务的提交可以以成员本人的 GitHub 账号出现，而不是某个机器人。运行一次 pal-arena github login --member <成员 ID> 授权；推送前后都会核对作者。`

> S5 出处：README / `docs/arena.md`。如果还没拿真实提交截图，就不要发这条。

---

## 6. 发布节奏

时间换算：太平洋时间（PT）上午 8–10 点 ≈ 北京时间晚上 23:00–01:00。调研里 Cline Kanban（≈PT 9 点）是一个例子，但样本太小，这只是经验建议，不是规律。

| 时间 | 动作 |
| --- | --- |
| **发布前 3–5 天** | 完成真实双机运行并录屏；导出 PNG；仓库首页自查：README 首屏、`LICENSE`、Release、示例入口（`/?demo=1`）能打开；个人主页置顶图和简介改好；准备好回复模板（见 §7）。 |
| **D0（建议下周二到周四）· PT 8:30（北京 23:30）** | 发英文 Thread A。第 1 条自带视频、不带链接；第 6 条放仓库链接；发完在第 1 条下自我回复一次完整链接并**置顶**。接下来 2 小时内逐条认真回复每个评论。 |
| D0 · 北京 20:00–21:00 | 发中文 Thread A，面向国内社群；在你常用的中文社群（即刻、V2EX、掘金、微信群等）发一句话链接。 |
| D1 | 单发 S1（机位轨道）。 |
| D2 | 英文 Thread B（自托管 + 免 API Key），面向 self-hosting 圈。 |
| D3 | S2 或 S3 的 GIF 小帖。 |
| D4 | 英文 Thread C（验收与“结果未知”）。这条最适合被转发和引战。 |
| D5 | 回顾帖：“发布一周的收获”：被问最多的 3 个问题 + 你改了什么。**用真实问题，没问题就别编。** |
| D6–D7 | 发布真实多机联调的结果（成功或失败都写）；如果出了 bug 就发修复说明。这是对 thread 末条那句承诺的兑现。 |
| 之后每周 | 一条“本周做了什么”（对应 CHANGELOG），一条 build-in-public 小帖。 |

**其他建议**
- **Show HN**：可以单独考虑，标题用“Show HN: Agent Cooperation – 把几台电脑上的 AI 代理组成一队，独立验收”这类平实写法，首条评论说明背景和已知限制。我没有核实竞品是否同步发过，所以这只是备选，由你决定；不要互相导流刷分。
- **@ 谁**：只 @ 真正用到或基于的项目，且不让对方被动背书。可以写“基于 PAL MCP Server”、“用 Tailscale 组网”，提到时放在文末，不要在首条 @ 大号求转。**不要私信求转发、不要买互动。**
- **别在发布当天改大功能**；如果“网页 GitHub 登录”恰好在发布前合并并发版，可以在 D3 单独发一条，不要塞进 Thread A。
- **度量**：只看三件事：仓库 star 的来源（GitHub Insights → Traffic）、有没有人真的跑通并留下 issue、评论里被问最多的问题。

---

## 7. 评论区答复模板（照实说）

- **“和 Cline Kanban / Conductor / Superset / Vibe Kanban 有什么不同？”**
  `据它们公开的介绍，这几个主要是在一台电脑上并行跑多个 agent。我们做的是跨多台电脑：执行机主动连中心领任务，每个任务一条分支，再由另一位成员独立验收。各有取舍，单机场景它们可能更成熟。`
- **“和 Cumora 有什么不同？”**
  `Cumora 我只看过它的公开介绍，不好替它下结论。我们的重点在：任务有验收条件、验收成员必须不是执行者、结果未知绝不自动重发写操作、Git 分支按提交 SHA 交接。`
- **“能在真机上用吗？”**
  `适配和流程已经写好并用假 CLI / 夹具测过；多台真机 + 真实订阅的完整联调还在进行，进展会公开发。`
- **“要 API Key 吗？”**
  `不要。用的是各台电脑上已经登录的客户端订阅；额度查不到时界面显示“未知”。`
- **“支持 X 客户端吗？”**
  照 README 客户端表原样回答，WorkBuddy 说“原型”，Blender 说“待联调”。
- **“安全吗？”**
  `中心只监听本机，Tailscale 加密并只让你网络里的设备访问，登录仍需令牌；不想共享时 tailscale serve reset。`

---

## 8. 发布前检查清单

- [ ] 所有图都标明“示例数据”或使用真实运行截图，且没有令牌、邮箱、私有仓库名、真实主机名。
- [ ] SVG 都导出了 PNG。
- [ ] 帖子里没有 star 数、用户数、“生产可用”“已在 N 台电脑验证”之类 README 里没有的说法。
- [ ] WorkBuddy 写“原型”，Blender 写“待联调”。
- [ ] 仓库链接可打开，`/?demo=1` 示例可打开。
- [ ] 评论回复模板已读熟，发布后 2 小时能在线。
- [ ] 如要提 GitHub 登录：确认已合并并发版。

---

## 9. 附：调研帖子链接速查

- Cline Kanban：https://x.com/cline/status/2037182739695493399
- Claude Code worktree：https://x.com/bcherny/status/2025007393290272904
- Browser Use：https://x.com/gregpr07/status/2100411066966749359
- OpenDots：https://x.com/ataiiam/status/2105710796198322659
- Cursor 自托管云 agent：https://x.com/cursor_ai/status/2095257412781396114
- Cline CLI 2.0：https://x.com/cline/status/2022341254965772367
- Cline Cloud Agents（thread 示例）：https://x.com/cline/status/2107909392649826516
- Cumora（宝玉）：https://x.com/dotey/status/2089404987587576191
- Superset（YC）：https://x.com/ycombinator/status/2059348845902115220
- Superset iPhone：https://x.com/ParadevAI/status/2102066246518976683
- Superset 搬运帖：https://x.com/igus_ai/status/2059352027352764492 、 https://x.com/mhdfaran/status/2030318538465141090
- Vibe Kanban 停运帖：https://x.com/tokengobbler/status/2042647208135123078 ；swyx：https://x.com/swyx/status/2050753293601935777
- 逛逛GitHub：https://x.com/guangGitHub/status/2012412068499259827
- OpenClaw 团队自述：https://x.com/steipete/status/2088473882357530979
- Conductor（YC 访谈）：https://x.com/ycombinator/status/2052827442503631171 ；融资文章：https://x.com/conductor_build/status/2039027752419098704
