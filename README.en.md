<p align="center">
  <img src="docs/images/hero.svg" alt="Agent Cooperation: you, your partners, a few computers, one dispatch console." width="100%">
</p>

<p align="center">
  <a href="https://github.com/Jay1023CN/agent-cooperation/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Jay1023CN/agent-cooperation?style=flat-square&color=1f3a5f"></a>
  <a href="LICENSE"><img alt="Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-1f3a5f?style=flat-square"></a>
  <img alt="macOS ARM64 / Windows x64" src="https://img.shields.io/badge/macOS%20ARM64%20%2F%20Windows%20x64-1f3a5f?style=flat-square">
  <img alt="Self-hosted" src="https://img.shields.io/badge/deploy-self--hosted-0b7a8c?style=flat-square">
</p>

<p align="center">
  <a href="README.md">简体中文</a> · <b>English</b>
</p>

<p align="center">
  <a href="https://github.com/Jay1023CN/agent-cooperation/releases/tag/v1.11.0">Download v1.11.0</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#how-it-works">Workflow</a> ·
  <a href="#supported-clients">Clients</a> ·
  <a href="#what-works-today">Status</a> ·
  <a href="#docs">Docs</a>
</p>

---

**Turn the AI coding clients on your computers into a team that divides work, hands over code, and reviews each other's results.**

Claude Code, Codex and Qoder can each do the work. Splitting tasks, carrying context between them and checking the results often still falls to you. Agent Cooperation brings that work into one dispatch console: you and your partners state a goal, a lead AI creates tasks, members on connected computers execute them, and a different member reviews the requirements and evidence.

Run your own hub and connect computers through Tailscale, without a public IP. Workers use local clients and their existing sign-in state. Harnesses such as DSH retain their own model, provider and credential configuration.

> **Current release: v1.11.0, early stage.** Windows and Mac packages are published and pass CI startup checks. A complete run across several real machines with real subscriptions is still pending. See [verification status](#what-works-today).

<table>
  <tr>
    <td width="33%" valign="top"><b>Goals become tasks</b><br><sub>The lead records dependencies, file scope and acceptance criteria. Scheduling records why each task was assigned.</sub></td>
    <td width="33%" valign="top"><b>Handoff by commit</b><br><sub>Each task has its own Git branch. Dependencies pin commit SHAs, with logs and artifacts returned alongside the work.</sub></td>
    <td width="33%" valign="top"><b>Independent review</b><br><sub>A different member checks requirements and evidence. Rejected work returns for up to three automatic revisions.</sub></td>
  </tr>
</table>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/arena-preview-dark.png">
  <img alt="Dispatch console: task pipeline, approval prompt, team channel and per-computer lanes" src="docs/images/arena-preview-light.png">
</picture>

<sub>Demo data showing the task pipeline, team channel, approvals, and running or queued work on each computer. The four computers shown are examples.</sub>

## Recent updates

- **Computers and members (v1.11.0):** redesigned machine cards, independent status indicators, readable routing preferences and narrow-screen/dark-theme layouts. An existing Claude Desktop session wrote the code through MCP handoff; Codex tested and accepted it.

- **Computer setup (v1.10.1):** commands follow the chosen OS and installation. Native packages no longer use Python commands. Client-specific fields, member cards and the command panel work across narrow screens and both themes.
- **Continuous handoff (v1.10.0):** targeted claims, progress, two-way messages and submit-and-wait. Optional notifications to existing Claude inboxes are disabled by default; a claim receipt is still required. See [desktop handoff](docs/desktop-handoff.md).

- **DSH harness support (v1.9.0):** ACP task sessions with custom profiles, model selection, permission requests and cancellation. Leaving the model blank uses the DSH profile; Agent Cooperation does not inject a DeepSeek official model configuration. See [DSH setup](docs/dsh-harness.md).
- **Task history:** execution and independent-review logs in task details, with paginated history and a refresh prompt when new records arrive.
- **Native delivery:** Windows x64, macOS ARM64 and a Mac setup helper are available. GitHub builds the packages, checks startup and hashes, and uploads release assets directly.

[v1.11.0 release notes](https://github.com/Jay1023CN/agent-cooperation/releases/tag/v1.11.0) · [Full changelog](CHANGELOG.md)

## Quick start

### 1. Download and start the hub

Use an always-on computer for the hub and connect other computers as workers. Native packages include Python and the compiled UI; running them needs no separate Python or Node.js installation. Code tasks need Git, and each worker needs its chosen clients installed and configured.

| Computer / purpose | v1.11.0 download |
| --- | --- |
| First-time setup on an Apple Silicon Mac mini | [Mac setup helper](https://github.com/Jay1023CN/agent-cooperation/releases/download/v1.11.0/Mac-mini-Setup-v1.11.0.zip) |
| Apple Silicon Mac, manual hub or worker setup | [macOS ARM64 package](https://github.com/Jay1023CN/agent-cooperation/releases/download/v1.11.0/pal-arena-darwin-arm64-ef8cf269019e.tar.gz) · [SHA-256](https://github.com/Jay1023CN/agent-cooperation/releases/download/v1.11.0/pal-arena-darwin-arm64-ef8cf269019e.tar.gz.sha256) |
| Intel / AMD Windows PC | [Windows x64 package](https://github.com/Jay1023CN/agent-cooperation/releases/download/v1.11.0/pal-arena-windows-amd64-ef8cf269019e.zip) · [SHA-256](https://github.com/Jay1023CN/agent-cooperation/releases/download/v1.11.0/pal-arena-windows-amd64-ef8cf269019e.zip.sha256) |

The Mac helper downloads and verifies the pinned package, then sets up the hub to start when the current user signs in. For manual setup, follow the [verification and extraction guide](docs/arena-delivery.md#校验和解压), keep the whole extracted directory, and run `Start Server.command` on Mac or `Start Server.cmd` on Windows from its outermost folder.

Open `http://127.0.0.1:8765` and sign in with the token in `~/.pal-arena/server/owner-token.txt`. On Windows, `~` is your user directory. Hub data is stored outside the application folder; see [upgrades and rollback](docs/arena-delivery.md#升级和回滚).

### 2. Connect the other computers

Install Tailscale on each computer and sign in. Enable sharing in the hub's Computers & members page, then copy the HTTPS address. From the native package's outermost folder you can also run `./pal-arena/pal-arena share` on Mac or `.\pal-arena\pal-arena.exe share` on Windows. The [network guide](docs/network.md) covers first-time HTTPS setup. The hub continues to listen only on localhost.

### 3. Add worker members

In Computers & members, create a separate token for each computer, select installed and configured clients, and download `worker.json`. Run these commands in that computer's native package folder:

<details>
<summary>Mac terminal</summary>

```sh
export ARENA_MACHINE_TOKEN='this-computer-token'
./pal-arena/pal-arena doctor --config worker.json
./pal-arena/pal-arena worker --config worker.json
```

</details>

<details>
<summary>Windows PowerShell</summary>

```powershell
$env:ARENA_MACHINE_TOKEN = 'this-computer-token'
.\pal-arena\pal-arena.exe doctor --config worker.json
.\pal-arena\pal-arena.exe worker --config worker.json
```

</details>

`doctor` sends no model tasks; a passing check does not confirm account access or quota. Start with a small task and check the result, files and independent review before adding concurrency. See [worker setup](docs/arena-delivery.md#安装检查和工作机) for token files and background services.

To use your GitHub identity for commits and pushes, configure a GitHub App for web sign-in, or run `pal-arena github login --member <member ID>` on each computer that pushes code. See [GitHub sign-in](docs/github-login.md).

<details>
<summary>Install from source (Python 3.10+)</summary>

```sh
git clone https://github.com/Jay1023CN/agent-cooperation.git
cd agent-cooperation
```

Mac / Linux:

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[platform]'
./.venv/bin/python -m arena serve
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[platform]'
.\.venv\Scripts\python.exe -m arena serve
```

Source-installed workers use the virtual environment's `python -m arena.worker --config worker.json`. The repository includes the compiled UI; Node.js is needed only when editing the frontend. See the [frontend notes](frontend/README.md).

</details>

To look around first, choose the demo on the sign-in page or open `/?demo=1`.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/flow-dark.svg">
  <img alt="Flow: you and partners state a goal, the lead splits it into tasks, tasks run in parallel on connected computers, a different member reviews, rejected work is revised up to three times, the lead summarises the delivery" src="docs/images/flow-light.svg" width="100%">
</picture>

1. **State a goal.** The team channel is visible to you and your partners. To think it through alone first, message the lead privately; private chats never change the shared requirements.
2. **Fix the requirement list.** Every item must be checkable, and the list is versioned. When it changes, acceptance results for older versions become void.
3. **Split and assign.** The lead writes capabilities, dependencies, file scope and acceptance criteria for each task. Scheduling applies hard filters first (online, role, capability, concurrency, which machine holds the code), then ranks by "balanced / quality-first / save quota", and **records the reason for every assignment**.
4. **Execute on each computer.** Workers connect to the hub and pull tasks. Each task runs in its own directory on an `arena/<task>/<attempt>` branch and returns a commit SHA, logs and artifacts. A dependent task reads the exact commit it depends on, not "whatever is latest".
5. **Independent review.** The reviewer cannot be the member who did the work. No structured verdict and no evidence means not passed. Failed tasks go back for revision, up to three automatic times, then stop and wait for a person.
6. **Deliver.** When everything passes, the lead integrates the result, aligned with the current requirement version. Merging and deploying follow the project's policy.

## Working rules

- **No pretending to be online.** A computer being online, a client being installed and a subscription being usable are three different states, shown separately. Machines that are not connected show an empty state, not demo data.
- **Unknown outcomes stop writes.** A write with an uncertain result is not automatically resent after a timeout or disconnect. Unknown quota remains unknown.
- **Memory needs a human.** "Remember this" proposals from the AI wait for a member's approval; every run pins the exact memory version it used.
- **Private means private.** Personal chats, their tasks, logs and artifacts are visible only to that person and cannot change shared requirements or confirm shared deliveries.

## Day-to-day

<details>
<summary>Shortcuts, notifications, GitHub sign-in and everyday actions</summary>

- **`Ctrl K` / `⌘ K` reaches everything**: pages, projects, tasks and actions (new task, connect a computer, switch channel, theme…) in one search box.
- **Keyboard is enough**: `N` new task, `/` message the lead, `G` then `C` `T` `R` `F` `M` for chat, tasks, review, artifacts, computers.
- **Unblock in place**: when an agent asks for permission, allow or deny right in the bar at the top; retry a failed task with one click.
- **Don't miss it in the background**: turn on desktop notifications for approvals and failures; the tab title shows the count.
- **Links are locations**: the address keeps the project, page and open task (`#/<project>/task/<task>`), so sharing, refreshing and bookmarking land in the same place.
- **Install it as an app**: use "Install" in the browser address bar, or "Add to Home Screen" on a phone.
- **Sign in with GitHub, push as yourself**: pick a member in project settings and task commits and pushes appear under their account, whichever computer ran the task.
- **Copy commands and IDs** from dialogs using their copy buttons.

</details>

## Supported clients

| Client | How it connects | Status |
| --- | --- | --- |
| Claude Code / Codex / Gemini | Official CLI, a new session per task | Usable; a desktop app being installed does not mean the CLI is |
| Qoder / Qoder CN | New session over ACP | Usable; does not take over chats already open in the IDE |
| Antigravity | Local desktop RPC | Usable; can start a new session or continue a specified existing one |
| OpenCode | Local HTTP service | Usable |
| DeepSeek Harness (DSH) | ACP task sessions, with an optional custom profile | Added in v1.9.0; protocol tests pass, real model routes await testing |
| WorkBuddy | Desktop MCP, the logged-in desktop session pulls tasks | Prototype; real client not yet verified on real machines |
| GLM and other models | Chosen in whichever client hosts them | A model name is not an executable |
| Blender | Assign to a computer with Blender; outputs `.blend` and exports | Dedicated adapter not yet integrated |

The upstream PAL `clink` tool and [PAL Agent Link](docs/agent-link.md) are kept: call other local agents from any MCP client, or generate MCP configs for [Multica](https://github.com/multica-ai/multica).

**DSH connects as a harness.** Configure models and providers in DSH; Agent Cooperation does not hardcode a DeepSeek official model. The bundled ACP profile has its own initial route, so configure it for your intended provider before use. See [DSH setup](docs/dsh-harness.md).

## What works today

- [x] Dispatch console web UI: projects, requirement versions, team channel / private chat, task board, review, artifacts, memory, run log; light and dark follow the system
- [x] Hub: token login, separate credentials for members and computers, SQLite storage, 5 GB staging budget with cleanup, consistent backups
- [x] Scheduling: hard filters + policy ranking + recorded reasons; dependencies wait for upstream review to pass
- [x] Git handoff: per-task directory and branch, commit SHA evidence, optional push to GitHub, a publisher that integrates reviewed commits
- [x] Independent review and up to three automatic revisions; writes stop when a result is unknown, never auto-repeated
- [x] [Continuous desktop handoff](docs/desktop-handoff.md): targeted claims, progress, two-way messages and submit-and-wait; cross-computer task occupancy registry
- [x] Commit and push under a member's own GitHub account, with the author checked before and after the push
- [x] Sign in with GitHub; the hub refreshes tokens and hands each task a short-lived push credential
- [x] One-command Tailscale sharing for the hub, with the address shown and pre-filled in the web UI
- [x] Paginated execution and review logs, with new-record notifications
- [x] DSH ACP support and custom profile configuration
- [x] Windows x64 / macOS ARM64 packages and a Mac helper, published directly from GitHub
- [ ] End-to-end runs on several real machines with real subscriptions
- [ ] WorkBuddy and Multica status write-back
- [x] An existing Claude Desktop session received an MCP task, changed UI code and returned commits for independent Codex acceptance ([record](docs/desktop-handoff.md#真实桌面验证))
- [ ] Dedicated Blender adapter and automatic checks that artifacts open

Verification for this release:

| Check | Result and evidence |
| --- | --- |
| Full GitHub test suite | [1,450 passed, 6 skipped](https://github.com/Jay1023CN/agent-cooperation/actions/runs/40809992730) |
| Cross-platform contracts | [Windows / Linux / macOS × Python 3.10 / 3.12: all six passed](https://github.com/Jay1023CN/agent-cooperation/actions/runs/40809992685) |
| Native builds and startup | [Both platforms passed; assets verified and published](https://github.com/Jay1023CN/agent-cooperation/actions/runs/40810200013) |
| Existing Claude desktop session | [Document claim and return verified](docs/claude-desktop-verification.md); automatic wake-up is not implemented |

Automated tests use temporary Git repositories, fake CLIs and protocol fixtures without consuming real subscriptions. Real DSH model routes, the complete multi-machine subscription workflow, WorkBuddy desktop execution and the dedicated Blender adapter still need device testing.

## Project layout

```
arena/         hub service: API, scheduling, storage, worker, maintenance and publishing
frontend/      web UI source (Vite + vanilla JS), built into arena/static/
agent_link/    PAL Agent Link: local client adapters, desktop handoff, cross-computer registry
clink/ tools/  CLI bridge and multi-model tools from upstream PAL MCP
docs/          documentation (mostly Chinese)
```

The hub and worker use Python, with FastAPI for the hub API and SQLite for storage. The frontend uses Vite and vanilla JavaScript; PyInstaller builds the native packages. Client connections use CLI, ACP, MCP, or local HTTP / RPC as appropriate.

Start UI changes in `frontend/`; see the [frontend notes](frontend/README.md).

## Docs

| Goal | Documentation |
| --- | --- |
| Install, connect and upgrade | [Setup](docs/arena.md) · [Delivery and upgrades](docs/arena-delivery.md) · [Tailscale](docs/network.md) |
| Configure clients and accounts | [DSH harness](docs/dsh-harness.md) · [Agent Link](docs/agent-link.md) · [GitHub sign-in](docs/github-login.md) |
| Understand task rules | [Product spec](docs/arena-product.md) · [Routing, memory and handoff](docs/arena-routing-memory.md) |
| Maintain and restore | [Maintenance and backups](docs/arena-maintenance.md) · [Occupancy registry](docs/coordination.md) |
| Desktop handoff and Multica | [Desktop handoff](docs/desktop-handoff.md) · [Multica](docs/multica.md) · [Integration plan](docs/multica-integration-plan.md) |
| Track releases | [Releases](https://github.com/Jay1023CN/agent-cooperation/releases) · [Changelog](CHANGELOG.md) |

Most documentation is currently in Chinese; issues in English are welcome.

## Credits and license

Built on [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server), under [Apache-2.0](LICENSE). [Multica](https://github.com/multica-ai/multica) is included as a submodule under its own [Multica License](https://github.com/multica-ai/multica/blob/8ffcc88/LICENSE).
