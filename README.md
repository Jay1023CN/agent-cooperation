# Agent Cooperation

**让多台电脑上的 AI 代理在同一个项目里协作。**

你和伙伴在工作台里讨论需求，负责人把工作拆给合适的代理：有人查资料、写文档，有人做网页和代码，有人用 Blender 建模。任务完成后，再由另一名成员检查成果，负责人汇总交付。

项目正在开发中。下面介绍的是目标工作流程；当前可用的接入方式和配置见[快速开始](#快速开始)。

## 从想法到交付

1. 创建项目，写下目标、GitHub 仓库和验收要求。
2. 和负责人讨论需求，由它拆分任务、安排依赖和选择执行成员。
3. 代理在各自的电脑上工作，通过 GitHub 分支和提交交接代码。
4. 另一名成员检查文件、运行结果和需求，负责人汇总成果。

工作台以 Multica 为基础，PAL Agent Link 负责连接本机代理。你和伙伴可以查看任务、成员状态、交接记录和成果，也可以随时调整方向。

## 客户端与工具

PAL Agent Link 提供本机代理的 MCP 接口：

| 客户端 | 接入方式 |
| --- | --- |
| Qoder / Qoder CN | 使用各自的 ACP CLI 创建代理会话。 |
| Antigravity | 连接本机桌面会话。 |
| OpenCode | 连接本机服务。 |
| WorkBuddy | 通过官方本地助理 API 接入；Arena 支持桌面会话主动领取任务。 |
| Codex / Claude / Gemini | 可使用 PAL 保留的 `clink` 工具。 |

项目也面向 Blender 等本机工具设计任务和产物交接。客户端、模型及订阅的使用方式见[接入评估](docs/arena-chinese-clients.md)。

## 界面预览

下面是 PAL Arena 界面原型，展示项目、任务和成员的组织方式。

![PAL Arena 浅色界面预览](docs/images/arena-preview-light.png)

[查看深色界面](docs/images/arena-preview-dark.png)

## 快速开始

需要 Python 3.10+。先在需要使用的客户端中完成登录，然后安装本项目：

```sh
git clone https://github.com/Jay1023CN/agent-cooperation.git
cd agent-cooperation
git submodule update --init third_party/multica
python -m pip install .
python -m agent_link --preflight
python -m agent_link --multica-config
```

在执行任务的电脑上生成 MCP 配置，并按照 [Multica 接入说明](docs/multica.md)添加到对应代理。其他配置和客户端连接方式见 [PAL Agent Link 说明](docs/agent-link.md)。

[整合方案](docs/multica-integration-plan.md) · [模型分配](docs/multica-model-routing.md) · [记忆与代码交接](docs/arena-routing-memory.md)

## 来源与许可

本项目基于 [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server)，遵循 [Apache-2.0 许可证](LICENSE)。[Multica](https://github.com/multica-ai/multica) 作为独立项目，以子模块引入，使用其自身的 [Multica License](https://github.com/multica-ai/multica/blob/8ffcc88/LICENSE)。
