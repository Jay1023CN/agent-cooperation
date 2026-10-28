<p align="center">
  <img src="docs/images/hero.svg" alt="Agent Cooperation: you, your partners, a few computers, one dispatch console." width="100%">
</p>

<p align="center">
  <a href="https://github.com/Jay1023CN/agent-cooperation/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Jay1023CN/agent-cooperation?style=flat-square&color=1f3a5f"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-1f3a5f?style=flat-square">
  <a href="LICENSE"><img alt="Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-1f3a5f?style=flat-square"></a>
  <img alt="No API key" src="https://img.shields.io/badge/API%20key-not%20needed-2e7d4f?style=flat-square">
  <img alt="Self-hosted" src="https://img.shields.io/badge/deploy-self--hosted-0b7a8c?style=flat-square">
</p>

<p align="center">
  <a href="README.md">简体中文</a> · <b>English</b>
</p>

---

You have Claude Code, Codex, Qoder, Antigravity, WorkBuddy… spread across a few Macs and Windows PCs. Each one is good at its job, but none of them knows the others exist. You split the tasks, copy context between them, remember who is doing what, and check whether "done" is really done.

**Agent Cooperation hands that job to a dispatch console.** You and your partners state a goal in the team channel. A lead AI turns it into tasks with acceptance criteria and assigns them to AI members on whichever of your computers are online. Each task runs on its own Git branch, then **a different member** reviews it against the requirements and the evidence; if it doesn't pass, it goes back for revision. It all runs on the client subscriptions you are already logged into. No model API keys.

> **Status: early.** The console, scheduling, Git handoff and review loop are built and tested with temporary Git repos, fake CLIs and protocol fixtures. A full end-to-end run across several real machines with real subscriptions is still in progress. See [What works today](#what-works-today).

<table>
  <tr>
    <td width="33%" valign="top"><b>🧭 One dispatch console</b><br><sub>State a goal in the team channel; a lead splits it into tasks with acceptance criteria and records why each one is assigned.</sub></td>
    <td width="33%" valign="top"><b>🔍 A different member reviews</b><br><sub>The worker can't review itself; no structured verdict and evidence means not passed, and it goes back for revision.</sub></td>
    <td width="33%" valign="top"><b>🔐 Self-hosted, no API key</b><br><sub>Uses the client subscriptions you're already logged into; Tailscale networking, no public IP.</sub></td>
  </tr>
</table>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/arena-preview-dark.png">
  <img alt="Dispatch console: task pipeline, approval prompt, team channel and per-computer lanes" src="docs/images/arena-preview-light.png">
</picture>

<sub>Demo data. The pipeline bar counts every task by state; "waiting for your approval" can be answered in place; the right column ("lanes") shows one lane per computer with running tasks laid out on it, and queued tasks below with what they are waiting for. The demo shows four computers; connect as many as you have.</sub>

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

## Compared with "just open more AI windows"

| | Manual | Agent Cooperation |
| --- | --- | --- |
| Who splits tasks | You | The lead, from the requirement list; tasks carry acceptance criteria |
| Who says it's done | The agent itself | Commits, logs and artifacts, then another member reviews |
| Disconnects / timeouts | You don't know if it ran | Marked "result unknown"; **write operations are never auto-resent** |
| Code handoff | Copy and paste | One branch per task, handed over by commit SHA |
| Usage | Everyone for themselves | Uses logged-in subscriptions; unknown quota is shown as "unknown" |
| Requirement changes | Everyone remembers a different version | Versioned; older acceptance results are voided |

A few more deliberate choices:

- **No pretending to be online.** A computer being online, a client being installed and a subscription being usable are three different states, shown separately. Machines that are not connected show an empty state, not demo data.
- **Memory needs a human.** "Remember this" proposals from the AI wait for a member's approval; every run pins the exact memory version it used.
- **Private means private.** Personal chats, their tasks, logs and artifacts are visible only to that person and cannot change shared requirements or confirm shared deliveries.

## Quick start

You need Python 3.10+, and each computer must already be logged into the clients you plan to use (Claude Code, Codex, Qoder, etc.).

**1. Start the hub on an always-on computer** (say, a Mac mini):

```sh
git clone https://github.com/Jay1023CN/agent-cooperation.git
cd agent-cooperation
python -m pip install .
pal-arena serve --share
```

Open `http://127.0.0.1:8765` and sign in with the token in `~/.pal-arena/server/owner-token.txt`. The hub only listens on localhost.

**2. Connect the other computers and your partners.** No public IP and no networking expertise needed: install Tailscale on every computer and sign in. `--share` gives the others an `https://….ts.net` address, shown at the top of "Computers & members" with a copy button. The first time, click "Enable HTTPS" once in Tailscale; the command gives you the link. Step-by-step: [network guide](docs/network.md) (Chinese).

**3. Connect a worker computer.** In the console go to "Computers & members", click "Connect a computer", generate a token for that machine, tick the clients installed there, and download `worker.json`. Then on that computer (Windows PowerShell: `$env:ARENA_MACHINE_TOKEN = '...'`):

```sh
export ARENA_MACHINE_TOKEN='this-computer-token'
pal-arena doctor --config worker.json
pal-arena-worker --config worker.json
```

`doctor` only checks installation and the local connection. It sends no model requests and does not mean your subscription quota is fine. Start with a small read-only task, check the result, files and review, then raise concurrency.

To have commits appear under your own GitHub account, run `pal-arena github login --member <your member ID>` on the computer that pushes code and authorize once in the browser.

Just want to look around? Click "Look at the demo without signing in" on the login page, or open `/?demo=1`.

## Supported clients

| Client | How it connects | Status |
| --- | --- | --- |
| Claude Code / Codex / Gemini | Official CLI, a new session per task | Usable; a desktop app being installed does not mean the CLI is |
| Qoder / Qoder CN | New session over ACP | Usable; does not take over chats already open in the IDE |
| Antigravity | Local desktop RPC | Usable; can start a new session or continue a specified existing one |
| OpenCode | Local HTTP service | Usable |
| WorkBuddy | Desktop MCP, the logged-in desktop session pulls tasks | Prototype; real client not yet verified on real machines |
| GLM and other models | Chosen in whichever client hosts them | A model name is not an executable |
| Blender | Assign to a computer with Blender; outputs `.blend` and exports | Dedicated adapter not yet integrated |

The upstream PAL `clink` tool and [PAL Agent Link](docs/agent-link.md) are kept: call other local agents from any MCP client, or generate MCP configs for [Multica](https://github.com/multica-ai/multica).

## What works today

- [x] Dispatch console web UI: projects, requirement versions, team channel / private chat, task board, review, artifacts, memory, run log; light and dark follow the system
- [x] Hub: token login, separate credentials for members and computers, SQLite storage, 5 GB staging budget with cleanup, consistent backups
- [x] Scheduling: hard filters + policy ranking + recorded reasons; dependencies wait for upstream review to pass
- [x] Git handoff: per-task directory and branch, commit SHA evidence, optional push to GitHub, a publisher that integrates reviewed commits
- [x] Independent review and up to three automatic revisions; writes stop when a result is unknown, never auto-repeated
- [x] Desktop handoff prototype and cross-computer task occupancy registry
- [x] Commit and push under a member's own GitHub account, with the author checked before and after the push
- [ ] End-to-end runs on several real machines with real subscriptions
- [ ] WorkBuddy and Multica status write-back
- [ ] Dedicated Blender adapter and automatic checks that artifacts open

Tests use temporary Git repos, fake CLIs and protocol fixtures, and do not consume real subscriptions. They are no substitute for real-device acceptance: the unchecked items above have not been verified on real devices.

## Project layout

```
arena/         hub service: API, scheduling, storage, worker, maintenance and publishing
frontend/      web UI source (Vite + vanilla JS), built into arena/static/
agent_link/    PAL Agent Link: local client adapters, desktop handoff, cross-computer registry
clink/ tools/  CLI bridge and multi-model tools from upstream PAL MCP
docs/          documentation (mostly Chinese)
```

To change the UI, only `frontend/` needs touching; see the [frontend notes](frontend/README.md).

## Docs

[Setup and running](docs/arena.md) · [Product spec](docs/arena-product.md) · [Delivery and upgrades](docs/arena-delivery.md) · [Maintenance and backups](docs/arena-maintenance.md) · [Routing, memory and code handoff](docs/arena-routing-memory.md) · [Desktop handoff](docs/desktop-handoff.md) · [Occupancy registry](docs/coordination.md) · [Agent Link](docs/agent-link.md) · [Multica](docs/multica.md) · [Changelog](CHANGELOG.md)

Most documentation is currently in Chinese; issues in English are welcome.

## Credits and license

Built on [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server), under [Apache-2.0](LICENSE). [Multica](https://github.com/multica-ai/multica) is included as a submodule under its own [Multica License](https://github.com/multica-ai/multica/blob/8ffcc88/LICENSE).
