"""Local MCP handoff between a Multica host and a desktop agent.

Both endpoints must use the same machine and SQLite database. This does not
verify Multica task lineage, reserve runs, or synchronize across machines.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP

from .claude_peer import ClaudePeer, ClaudePreflightError
from .coordination_client import CoordinationClient, CoordinationError
from .git_evidence import inspect_git_delivery
from .handoff_channel import HandoffChannel
from .handoff_store import HandoffStore
from .lease_keeper import LeaseKeeper
from .multica_runs import fetch_verified_lineage


def build_server(
    store: HandoffStore,
    role: str,
    verifier: Callable | None = None,
    reserve: Callable | None = None,
    inspect_reservation: Callable | None = None,
    check_reservation: Callable | None = None,
    keeper: LeaseKeeper | None = None,
    claude_peer: ClaudePeer | None = None,
) -> FastMCP:
    if role not in ("host", "desktop"):
        raise ValueError("Role must be host or desktop")
    channel = HandoffChannel(store)
    mcp = FastMCP(
        "local-desktop-handoff",
        instructions=(
            "Use the existing conversation, never a new model session. Desktop consumers can stay available with "
            "next_task, report progress and read host messages with wait_for_update, then complete_and_wait. "
            "Keep a stable consumer_id and a unique request_id per next task; retry a lost response using that same ID. "
            "A recovered receipt is not permission to repeat work. Continue waiting only while the user authorizes it. "
            "Only the host renews leases. Cancellation and expiry require stopping work; submitted results still need "
            "independent acceptance. This server does not wake a disconnected or idle desktop application."
        ),
    )

    @mcp.tool()
    def channel_capabilities() -> dict:
        """Describe the live handoff protocol without contacting a model or desktop application."""
        return {
            "version": 3,
            "role": role,
            "max_wait_seconds": 25,
            "messages": True,
            "message_acknowledgements": True,
            "continuous_handoff": True,
            "application_wakeup": False,
            "claude_peer_notification": role == "host" and claude_peer is not None,
        }

    @mcp.tool()
    def post_message(lineage: str, message_id: str, text: str, kind: str = "note", handle: str = "") -> dict:
        """Send a durable task clarification (host) or progress (desktop), using an idempotent message_id.

        Desktop callers need the claim handle. Messages never renew leases or reopen cancelled tasks.
        """
        return store.post_message(role, lineage, message_id, text, kind, handle)

    @mcp.tool()
    def read_messages(lineage: str, after: int = 0, limit: int = 50, handle: str = "") -> dict:
        """Read peer messages using a durable cursor. Desktop callers need the task's claim handle."""
        return store.read_messages(role, lineage, after, limit, handle)

    @mcp.tool()
    async def wait_for_update(
        lineage: str, after: int = 0, known_state: str = "claimed", wait_seconds: float = 25, handle: str = ""
    ) -> dict:
        """Wait up to 25 seconds for peer messages, cancellation, expiry or a submitted result.

        Store next_after as the next cursor. Timeout is not task completion or renewed authority.
        """
        return await channel.wait_for_update(role, lineage, after, known_state, wait_seconds, handle)

    if role == "host":

        if claude_peer is not None:

            @mcp.tool()
            def list_claude_sessions() -> dict:
                """List verified local inboxes without starting a session or reading conversation history."""
                return {"sessions": claude_peer.list_sessions()}

            @mcp.tool()
            def notify_claude_task(lineage: str, session_id: str, expected_cwd: str) -> dict:
                """Notify an explicitly selected existing Claude session once for this pending handoff.

                Requires user authorization to message that session. Uses peer authentication and preserves
                receiver permission controls. transport_written is NOT delivered: inspect the claim receipt.
                An uncertain attempt is never automatically resent, even after reconnecting.
                """
                notification = store.begin_notification(lineage, session_id)
                if notification["recovered"]:
                    return {**notification, "delivered": False}
                payload = notification["payload"]
                receipt_id = hashlib.sha256(lineage.encode()).hexdigest()[:32]
                entry = (
                    ["internal-handoff-client"]
                    if getattr(sys, "frozen", False)
                    else ["-B", "-X", "utf8", "-m", "agent_link.handoff_client"]
                )
                client = [sys.executable, *entry, "--db", store.path]
                content = (
                    "来自 Agent Cooperation 的 Codex 协作者。请在当前会话接收前端代码任务，直接实施并提交候选结果；"
                    "宿主负责测试和最终验收。不要启动新模型进程，不要改变权限或配置。\n"
                    f"交接 lineage={lineage}，工作目录={payload.get('workspace')}。\n"
                    "使用已连接的 MCP next_task，或 Pal agentlink 的 handoff_next；若当前会话工具定义还是旧版，"
                    "可运行下面已有的本机 MCP 传输客户端，它只启动 stdio 服务，不启动模型。\n"
                    f"客户端命令前缀（参数数组）={json.dumps(client, ensure_ascii=False)}\n"
                    f"源码运行目录={Path(__file__).resolve().parent.parent}\n"
                    f"next_task 参数={json.dumps({'consumer_id': payload.get('recipient') or 'claude-ui', 'request_id': 'notify-' + receipt_id, 'wait_seconds': 0}, ensure_ascii=False)}\n"
                    "客户端用法：在命令前缀后追加工具名称和一个 JSON 参数字符串；也可追加工具名称和 '-'，从标准输入传 JSON。"
                    "返回值包含完整任务说明和 handle；保存领取凭据，先检查状态再改代码，"
                    "用 report_progress 回报，通过 wait_for_update 读取补充，处理每条后用 acknowledge_message 确认；"
                    "取消或过期立即停止。"
                    "完成后使用 complete_and_wait 提交 summary 和 evidence；request_id 使用下一次领取的新 ID。"
                    "收到任务后先发进度回执，不能把收到此消息当作已领取或已验收。"
                )
                try:
                    result = claude_peer.send(session_id, expected_cwd, content, "handoff-" + receipt_id)
                except ClaudePreflightError:
                    store.discard_unsent_notification(lineage, session_id)
                    raise
                store.finish_notification(lineage, session_id)
                return result

        @mcp.tool()
        def offer_task(
            lineage: str, task_id: str, workspace: str, prompt: str, issue_id: str = "", recipient: str = ""
        ) -> dict:
            """Offer once after independently verifying the full Multica retry lineage and workspace.

            Do not include tokens. A local database cannot prevent retries on another computer.
            """
            path = Path(workspace).expanduser()
            if not path.is_absolute() or not path.is_dir() or not prompt.strip():
                raise ValueError("An existing absolute workspace and nonempty prompt are required")
            if verifier is not None and verifier(task_id, issue_id)["lineage"] != lineage:
                raise ValueError("Lineage does not match the Multica task history")
            if reserve is not None:
                reserve(lineage, task_id, store.instance_id())
                if verifier is None or verifier(task_id, issue_id)["lineage"] != lineage:
                    raise ValueError("Multica task could not be rechecked after reservation")
            payload = {"workspace": str(path.resolve()), "prompt": prompt}
            if recipient:
                payload["recipient"] = recipient
            state = store.create(lineage, task_id, payload)
            if keeper is not None and state in ("pending", "claimed"):
                keeper.start(lineage, task_id, issue_id)
            return {"state": state, "task_id": task_id}

        @mcp.tool()
        def list_listeners() -> dict:
            """Show consumers currently waiting in next_task, not merely configured clients or open apps."""
            return {"listeners": store.listeners()}

        @mcp.tool()
        def inspect_task(lineage: str) -> dict:
            """Read a local handoff and candidate result; this does not update Multica."""
            return store.inspect(lineage) or {"state": "missing"}

        @mcp.tool()
        def verify_git_delivery(
            lineage: str, expected_commit: str, base_commit: str, expected_author: dict | None = None
        ) -> dict:
            """Check submitted Git state and changed files without running project tests."""
            return inspect_git_delivery(
                store.submitted_workspace(lineage), expected_commit, base_commit, expected_author=expected_author
            )

        @mcp.tool()
        def renew_task(lineage: str, task_id: str, issue_id: str = "") -> dict:
            """Renew for 60 seconds only after verifying this exact Multica run is still active.

            Renew frequently; an expired lease cannot be revived automatically.
            """
            if verifier is not None and verifier(task_id, issue_id)["lineage"] != lineage:
                raise ValueError("Lineage does not match the Multica task history")
            if check_reservation is not None:
                check_reservation(lineage, task_id, store.instance_id())
            return {"lease_until": store.renew(lineage, task_id)}

        @mcp.tool()
        def cancel_task(lineage: str) -> dict:
            """Stop accepting a result; a desktop session may still be running."""
            state = store.cancel(lineage)
            if keeper is not None:
                keeper.stop(lineage)
            return {"state": state}

        if inspect_reservation is not None:

            @mcp.tool()
            def inspect_coordinator(lineage: str) -> dict:
                """Inspect a central reservation; it does not authorize a retry."""
                return inspect_reservation(lineage)

    else:

        @mcp.tool()
        def acknowledge_message(lineage: str, handle: str, message_id: str) -> dict:
            """Confirm one host message after processing it; retries return the existing receipt."""
            return store.acknowledge_message(lineage, handle, message_id)

        @mcp.prompt()
        def cooperate(consumer_id: str = "claude-ui") -> str:
            """Start an authorized continuous collaboration loop in this existing conversation."""
            store._identifier(consumer_id, "consumer_id")
            return (
                f"在当前会话中作为 {consumer_id} 持续协作，不新开模型进程。"
                "调用 next_task，consumer_id 固定，每次新领取使用新的 request_id；响应丢失时用原 ID 恢复。"
                "领取后先检查状态，按任务范围直接编写代码，并通过 report_progress 回报进展；"
                "用 wait_for_update 接收宿主补充消息，处理每条后用 acknowledge_message 确认；取消或过期立即停止。"
                "完成后调用 complete_and_wait 回传 summary、非空 evidence，并等待下一项任务。"
                "若 next 中已包含任务，直接处理，不再次领取。recovered=true 时先核对已有工作，不能重复执行。"
                "idle 只表示本次等待超时；仅在用户仍授权持续协作时继续等待，用户要求停止时退出。"
                "宿主负责后端、测试和验收；候选结果提交不代表已通过验收。"
            )

        @mcp.tool()
        def report_progress(lineage: str, handle: str, message_id: str, summary: str) -> dict:
            """Return a durable progress receipt to the host. Progress does not mean accepted or completed."""
            return store.post_message("desktop", lineage, message_id, summary, "progress", handle)

        @mcp.tool()
        async def next_task(consumer_id: str, request_id: str, wait_seconds: float = 25) -> dict:
            """Wait and atomically claim an eligible task for this conversation.

            Use one stable consumer_id and preserve request_id after a lost response. Idle means no task arrived;
            call again only while the user authorizes continued cooperation. Never reexecute a recovered receipt.
            """
            return await channel.next_task(consumer_id, request_id, wait_seconds)

        @mcp.tool()
        async def complete_and_wait(
            lineage: str,
            handle: str,
            summary: str,
            evidence: list[str],
            consumer_id: str,
            request_id: str,
            wait_seconds: float = 25,
        ) -> dict:
            """Submit this consumer's candidate and wait for its next task in the same tool call.

            request_id identifies the NEXT claim; preserve all arguments when recovering an uncertain response.
            Read and acknowledge each host message before submitting. The host remains responsible for acceptance.
            """
            return await channel.complete_and_wait(
                lineage, handle, summary, evidence, consumer_id, request_id, wait_seconds
            )

        @mcp.tool()
        def list_tasks() -> dict:
            """List pending local handoffs without claiming one."""
            return {"tasks": store.pending()}

        @mcp.tool()
        def claim_task(lineage: str) -> dict:
            """Claim a task once; lost responses require inspection, not another claim."""
            return store.claim(lineage)

        @mcp.tool()
        def task_status(lineage: str, handle: str) -> dict:
            """Check whether a claimed task was cancelled before continuing work."""
            return {"state": store.desktop_state(lineage, handle)}

        @mcp.tool()
        def submit_result(lineage: str, handle: str, summary: str, evidence: list[str]) -> dict:
            """Submit a candidate result for the host to inspect and independently review."""
            if not summary.strip() or not evidence or not all(isinstance(x, str) and x.strip() for x in evidence):
                raise ValueError("A summary and at least one evidence item are required")
            store.submit(lineage, handle, {"summary": summary, "evidence": evidence})
            return {"state": "submitted"}

    return mcp


def main():
    parser = argparse.ArgumentParser(description="Local desktop handoff MCP")
    parser.add_argument("--db", required=True, help="Absolute path to a private shared SQLite database")
    parser.add_argument("--role", required=True, choices=("host", "desktop"))
    parser.add_argument(
        "--claude-peer", action="store_true", help="Opt in to notifying an existing local Claude inbox (host only)"
    )
    parser.add_argument("--multica-task", action="store_true", help="Verify host offers and renewals with Multica")
    parser.add_argument("--coordinator-url", help="Single shared reservation service; requires --multica-task")
    parser.add_argument(
        "--print-config", action="store_true", help="Print MCP configuration without starting the service"
    )
    args = parser.parse_args()
    path = Path(args.db).expanduser()
    if not path.is_absolute() or not path.parent.is_dir():
        parser.error("--db must be an absolute path inside an existing private directory")
    if args.coordinator_url and not args.multica_task:
        parser.error("--coordinator-url requires --multica-task")
    if args.multica_task and args.role != "host":
        parser.error("--multica-task is only valid for the host")
    if args.claude_peer and args.role != "host":
        parser.error("--claude-peer is only valid for the host")
    if args.print_config:
        entry = ["internal-handoff"] if getattr(sys, "frozen", False) else ["-m", "agent_link.desktop_handoff"]
        command_args = [*entry, "--db", str(path.resolve()), "--role", args.role]
        if args.multica_task:
            command_args.append("--multica-task")
        if args.claude_peer:
            command_args.append("--claude-peer")
        if args.coordinator_url:
            try:
                CoordinationClient(args.coordinator_url, "x" * 32)
            except CoordinationError:
                parser.error("invalid coordinator URL")
            command_args.extend(["--coordinator-url", args.coordinator_url])
        print(
            json.dumps(
                {
                    "mcpServers": {
                        "desktop-handoff": {"command": sys.executable, "args": command_args, "env": {"PYTHONUTF8": "1"}}
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    verifier = None
    if args.multica_task:
        required = (
            "MULTICA_SERVER_URL",
            "MULTICA_TOKEN",
            "MULTICA_TASK_ID",
            "MULTICA_AGENT_ID",
            "MULTICA_WORKSPACE_ID",
        )
        if not all(os.environ.get(name) for name in required):
            parser.error("--multica-task requires the current Multica task environment")
        task_id = os.environ["MULTICA_TASK_ID"]

        def verifier(requested_id, issue_id):
            if requested_id != task_id:
                raise ValueError("Task ID does not match the current Multica run")
            return fetch_verified_lineage(
                server_url=os.environ["MULTICA_SERVER_URL"],
                token=os.environ["MULTICA_TOKEN"],
                task_id=task_id,
                agent_id=os.environ["MULTICA_AGENT_ID"],
                workspace_id=os.environ["MULTICA_WORKSPACE_ID"],
                issue_id=issue_id,
            )

    reservation = None
    inspection = None
    reservation_check = None
    if args.coordinator_url:
        coordinator = CoordinationClient(args.coordinator_url, os.getenv("COOPERATION_COORDINATION_TOKEN", ""))

        def reservation(lineage, requested_id, instance_id):
            coordinator.reserve(os.environ["MULTICA_WORKSPACE_ID"], lineage, requested_id, instance_id)

        def inspection(lineage):
            return coordinator.inspect(os.environ["MULTICA_WORKSPACE_ID"], lineage)

        def reservation_check(lineage, requested_id, instance_id):
            coordinator.check(os.environ["MULTICA_WORKSPACE_ID"], lineage, requested_id, instance_id)

    store = HandoffStore(path)
    keeper = LeaseKeeper(store, verifier, reservation_check) if verifier and reservation_check else None
    if keeper is not None:
        atexit.register(keeper.close)
    peer = ClaudePeer() if args.claude_peer else None
    build_server(store, args.role, verifier, reservation, inspection, reservation_check, keeper, peer).run(
        transport="stdio"
    )


if __name__ == "__main__":
    main()
