# Agent Cooperation · 多代理协作开发平台

**你和伙伴在一个工作台讨论需求，让多台电脑上的代理分工、交接，并由独立成员验收实际成果。**

面向比赛和小团队项目：把研究、文档、网页、代码、调试与 Blender 建模放到同一套工作流程中，尽量使用各台电脑已有的客户端和订阅。

[整合方案](docs/multica-integration-plan.md) · [Multica 接入](docs/multica.md) · [模型分配建议](docs/multica-model-routing.md) · [记忆与代码交接](docs/arena-routing-memory.md) · [客户端接入评估](docs/arena-chinese-clients.md)

> **当前状态：开发中。** Multica 与 PAL 的配置入口、独立的 Arena 原型和多项自动化测试已存在；完整整合、真实账号及四台电脑的联调尚未完成。这里的代码还不能视作安装后即可完成全部目标的最终交付。

## 使用体验

你或伙伴提出目标，负责人整理需求和验收标准，把任务拆给擅长的成员。各成员在自己所在的电脑上工作，代码通过 GitHub 分支和明确的提交交接；另一名成员检查实际文件、运行结果和需求，再由负责人汇总。

```text
你和伙伴
    │ 讨论需求、查看进度与验收结果
    ▼
Multica 工作台（计划运行在常开的 Mac mini）
    │ 负责人拆分任务，记录状态和结果
    ▼
各台电脑上的 Multica 执行环境
    ├── 原生 CLI 代理
    └── PAL Agent Link ── 本机桌面客户端或 CLI
                │
                └── GitHub 分支 / 提交 / 产物 ── 独立验收 ── 汇总
```

无公网 IP 的 Mac mini 计划通过私有网络供两人访问；真实网络、账号、客户端以及权限还需要在你们的设备上验证。

## 界面预览

这是本仓库 **PAL Arena 独立原型**的效果预览，示例任务和电脑状态均为演示数据；它尚未与 Multica 的任务状态联通。

![PAL Arena 浅色界面预览](docs/images/arena-preview-light.png)

[查看深色界面](docs/images/arena-preview-dark.png)

## 项目由什么组成

| 部分 | 作用与位置 |
| --- | --- |
| Multica | 计划作为唯一的任务工作台和跨电脑执行协调中心；源码由 [Multica 上游](https://github.com/multica-ai/multica)单独维护，本仓库的 `third_party/multica` 以子模块固定参考版本。 |
| PAL Agent Link | 本仓库的 `agent_link/`；连接本机代理，提供 MCP 工具与客户端适配。 |
| PAL 原有工具 | 保留 `clink` 和原有模型协作工具；是否需要供应商 API 取决于具体工具，桌面客户端桥接本身不要求购买新的模型 API。 |
| PAL Arena 原型 | 本仓库的 `arena/` 与独立 `frontend/`；已探索调度、记忆、任务、验收和桌面交接，**目前是独立原型，不是已接入 Multica 的第二个控制台**。 |

整合时以 Multica 为任务、运行状态和人员的唯一来源；Arena 中合适的逻辑会按接口和数据边界逐项复用，避免两个控制台重复派发同一任务。前端保持独立，字体、字号、间距和组件使用统一规范，浅色和深色跟随系统。

## 客户端与模型

| 目标 | 计划接入与限制 |
| --- | --- |
| Qoder / Qoder CN | Multica 上游支持各自的 CLI 运行时；PAL 也有 ACP 桥接。CLI 需要单独确认安装、登录和订阅权限，不会接管已打开的 IDE 对话。上游当前直接运行 Qoder 时默认使用 `--yolo`，整合时需明确权限方式。 |
| WorkBuddy 桌面版 | Arena 有由桌面会话主动领取任务的 MCP 原型；接入 Multica 的交接和完成状态尚需开发及实机验证。 |
| Antigravity | Multica 上游支持 `agy` CLI；PAL 另有连接本机桌面会话的适配。两者是不同的使用方式，取决于实际安装和账号。 |
| Codex / Claude / Gemini | 优先使用各客户端明确支持的本机运行方式和已有登录态；桌面订阅是否包含相应 CLI 需各自确认。 |
| GLM | 作为某个真实客户端提供的模型选择，不能仅凭模型名称创建一个可运行的代理。 |
| Blender | 由已安装 Blender 的执行电脑处理，交付可打开的文件；专用适配及实机验收仍在计划中。 |

订阅、可用模型、剩余额度和额外费用以各客户端实际情况为准；平台不会把未知额度显示为可用，也不会默认使用收费 API 或云任务。详见[客户端接入评估](docs/arena-chinese-clients.md)。

## 分工、记忆与代码

- **分配**：PAL MCP 已提供只读的成员筛选和排序建议，根据调用方提供的在线状态、能力、并发、模型上下文、额度状态及质量／经济性偏好解释选择原因；自动采集状态和实际派发仍需与 Multica 整合。
- **记忆**：项目约定与个人偏好分开；长期记忆由成员确认并保留版本，当前需求优先。Arena 已有相关原型，接入 Multica 仍需完成。
- **代码**：任务使用独立工作区和分支，以提交 SHA 交接；冲突和状态不明时保留现场，避免重复写入。实际验收需基于提交、文件和检查结果。
- **验收**：安排不同的执行成员检查需求与成果，记录证据；需求发生变化时重新核对。自动验收与 Multica 的完整联动仍待开发。
- **存储**：Mac mini 中心按 5 GB 暂存目标设计，GitHub 仓库和各机缓存另计；Multica 数据库、容器镜像等实际占用需在安装前测量，这不是系统磁盘的硬配额。

## 当前可开发的部分

本仓库保留 PAL 原有安装方式，也提供本机 Agent Link 的 Multica MCP 配置生成：

```sh
# 按需获取固定版本的 Multica 源码；这一步不会部署服务
git submodule update --init third_party/multica
python -m pip install .
python -m agent_link --preflight
python -m agent_link --multica-config
```

配置需要在**实际执行任务的电脑**生成；它不会自动安装 Multica、登录客户端或完成跨电脑部署。开发方式、前置条件和验收步骤见 [Multica 接入说明](docs/multica.md) 与 [PAL Agent Link 说明](docs/agent-link.md)。

## 来源与许可

本仓库基于 [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server) 开发，保留上游工具与 [Apache-2.0 许可证](LICENSE)。[Multica](https://github.com/multica-ai/multica) 是独立项目，使用包含附加条件的 [Multica License](https://github.com/multica-ai/multica/blob/8ffcc88/LICENSE)，其中包括界面品牌保留和向第三方提供托管服务的限制；不能把它简单视为本仓库的 Apache-2.0 授权。各客户端由其提供方独立提供。
