"""A shared, user-invoked MCP worker for an already signed-in desktop client.

The desktop client must actively call next_task; the bridge never takes over a
chat or turns an ordinary assistant message into a completion signal.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from agent_link.acp_client import ClientResources
from agent_link.common import BridgeError

from .blender import BlenderCheckError, verify_exports
from .worker import API, RunCancelled, Worker, WorkerError, load_config, run_process
from .workspaces import workspace_lock


def desktop_environment():
    """Give approved commands basic OS settings without inheriting unrelated credentials."""
    allowed = {
        "PATH",
        "PATHEXT",
        "HOME",
        "USER",
        "LOGNAME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "SHELL",
        "COMSPEC",
        "SYSTEMROOT",
        "WINDIR",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LANGUAGE",
        "TZ",
        "TERM",
        "COLORTERM",
        "NO_COLOR",
        "FORCE_COLOR",
        "OS",
        "NUMBER_OF_PROCESSORS",
        "PYTHONUTF8",
        "LD_LIBRARY_PATH_ORIG",
        "DYLD_LIBRARY_PATH_ORIG",
        "LIBPATH_ORIG",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed or key.upper().startswith("LC_")}


class DesktopBridge:
    """One registered GUI identity, one in-flight task, and no exposed bearer credentials."""

    def __init__(self, config, token):
        agents = config.get("agents", [])
        if len(agents) != 1 or agents[0].get("provider") != "desktop_mcp":
            raise WorkerError("Desktop MCP configuration requires exactly one desktop_mcp agent")
        if config.get("slots") != 1 or agents[0].get("roles") != ["worker"] or agents[0].get("mode") != "new":
            raise WorkerError("Desktop MCP supports one shared worker, not a reviewer or publisher")
        if not token.strip():
            raise WorkerError("A dedicated machine token is required")
        self.config = config
        self.worker = Worker(config, token=token, desktop_bridge=self)
        self.lock = threading.RLock()
        self.handoff = None
        self.closed = False
        self.enabled = agents[0].get("enabled", True)
        self.claim_uncertain = False

    def _set_enabled(self, enabled):
        self.config["agents"][0]["enabled"] = enabled
        self.worker.register()

    def next_task(self):
        """Claim only while a GUI is actively asking; a second conversation cannot claim the same handle."""
        with self.lock:
            if self.closed:
                raise WorkerError("Desktop bridge is closed")
            if not self.enabled:
                raise WorkerError("The desktop agent is disabled in its local configuration")
            if self.claim_uncertain:
                raise WorkerError(
                    "A previous claim response was lost; inspect the platform before restarting this bridge"
                )
            handoff = self.handoff
            if handoff and handoff["done"].is_set():
                self.handoff = handoff = None
            if handoff and handoff.get("delivered"):
                session = handoff.get("session") or {}
                return {
                    "state": "busy",
                    "task_id": session.get("task_id"),
                    "attempt": session.get("attempt"),
                    "message": "任务已交给桌面会话；若领取结果丢失，请先确认原会话已停止，再请求恢复。",
                }
            if not handoff:
                handoff = {"ready": threading.Event(), "done": threading.Event(), "delivered": False}
                self.handoff = handoff
                claim_started = False
                try:
                    self._set_enabled(True)
                    claim_started = True
                    job = self.worker.machine.request("POST", "/api/machines/claim", {})
                except (WorkerError, httpx.HTTPError):
                    # A lost claim response is ambiguous. Never submit another claim automatically.
                    self.claim_uncertain = claim_started
                    self.handoff = None
                    raise
                finally:
                    # Remove idle GUI identities from routing as soon as this request ends.
                    with contextlib.suppress(WorkerError, httpx.HTTPError):
                        self._set_enabled(False)
                if not job:
                    self.handoff = None
                    return {"state": "empty", "message": "当前没有可由此成员领取的共享任务。"}
                handoff["task_id"] = job["task"]["id"]

                def work():
                    try:
                        self.worker.run_one(job)
                    finally:
                        handoff["done"].set()
                        handoff["ready"].set()

                threading.Thread(target=work, daemon=True).start()

        handoff["ready"].wait(20)
        with self.lock:
            session = handoff.get("session")
            if handoff["done"].is_set() and not session:
                return {"state": "failed", "message": "准备工作区失败，请在平台中查看任务记录。"}
            if not session:
                return {"state": "preparing", "message": "正在准备任务工作区，请稍后再次调用 next_task。"}
            if handoff["delivered"]:
                return {"state": "busy", "message": "任务已经交给此桌面会话。"}
            if not session["accepting"].is_set() or session["cancel"].is_set():
                return {"state": "stopped", "message": "任务已停止，请在平台中查看。"}
            handoff["delivered"] = True
            return self._payload(session)

    @staticmethod
    def _payload(session):
        return {
            "state": "ready",
            "handle": session["handle"],
            "nonce": session["nonce"],
            "task_id": session["task_id"],
            "attempt": session["attempt"],
            "requirement_revision": session["revision"],
            "workspace": str(session["workspace"]),
            "prompt": session["prompt"],
            "instructions": (
                "这是共享项目任务。只在上面的工作区工作，优先使用本 MCP 的文件和命令工具；"
                "命令和执行配置修改需由平台成员确认。完成后调用 submit_result；"
                "工作期间调用 task_status 查看是否停止，收到 stopped 后立即停止修改。"
                "聊天里的完成声明不会提交任务，提交后还需要其他成员独立验收。"
            ),
        }

    def execute(self, job, workspace, prompt, api, cancel, on_output, timeout):
        task = job["task"]
        if task["channel"] != "shared" or task["kind"] != "worker":
            raise WorkerError("Only shared worker tasks may enter a persistent desktop session")
        session = {
            "handle": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "task_id": task["id"],
            "attempt": task["attempt"],
            "revision": task["revision"],
            "capability": task.get("capability"),
            "workspace": Path(workspace).resolve(),
            "prompt": prompt,
            "cancel": cancel,
            "accepting": threading.Event(),
            "submitted": threading.Event(),
            "api": api,
            "action_api": API(self.config["server"], job["agent_token"], self.config.get("allow_http", False)),
            "state_lock": threading.RLock(),
        }
        session["accepting"].set()

        def resolve(identifier):
            if identifier != session["handle"] or not session["accepting"].is_set() or cancel.is_set():
                raise BridgeError("This task is no longer accepting desktop operations")
            return {"workspace": str(session["workspace"])}

        def authorize(identifier, kind, details):
            resolve(identifier)
            permission = uuid.uuid4().hex
            api.request(
                "POST",
                "/api/runs/permissions",
                {
                    "permissions": [
                        {
                            "id": permission,
                            "tool": {
                                "title": {
                                    "terminal": "桌面客户端运行命令",
                                    "recovery": "恢复桌面任务（确认原会话已停止）",
                                }.get(kind, "桌面客户端修改执行配置"),
                                "kind": "execute" if kind in ("terminal", "recovery") else "edit",
                                "rawInput": details,
                            },
                            "options": [
                                {"optionId": "allow_once", "name": "允许本次操作", "kind": "allow_once"},
                                {"optionId": "reject_once", "name": "拒绝本次操作", "kind": "reject_once"},
                            ],
                        }
                    ]
                },
            )
            deadline = time.monotonic() + min(timeout, 600)
            while time.monotonic() < deadline:
                resolve(identifier)
                for item in api.request("GET", "/api/runs/permissions").get("decisions", []):
                    if item["id"] == permission:
                        if item.get("option_id") == "allow_once":
                            return
                        raise BridgeError("The platform member rejected this operation")
                cancel.wait(1)
            raise BridgeError("Platform approval timed out")

        session["authorize"] = authorize
        session["resources"] = ClientResources(resolve, authorize, environment=desktop_environment())
        session["resources"].begin(session["handle"])
        with self.lock:
            handoff = self.handoff
            if not handoff or handoff.get("task_id") != task["id"] or self.closed:
                session["action_api"].close()
                raise WorkerError(
                    "Desktop handoff ended before the workspace was ready", evidence={"execution_uncertain": True}
                )
            handoff["session"] = session
            handoff["ready"].set()
        on_output("桌面客户端已取得共享任务，等待明确提交。\n")
        try:
            deadline = time.monotonic() + timeout
            while not session["submitted"].wait(0.5):
                if cancel.is_set() or self.closed:
                    raise RunCancelled("Desktop task stopped; inspect the desktop session before retrying")
                if time.monotonic() >= deadline:
                    raise WorkerError(
                        "Desktop task timed out without explicit submission; inspect it before retrying",
                        evidence={"execution_uncertain": True},
                    )
            if cancel.is_set() or self.closed:
                raise RunCancelled("Desktop task stopped before the submitted result was captured")
            if session.get("error"):
                raise WorkerError(session["error"], evidence={"execution_uncertain": True})
            return session["result"], {"desktop_handoff": "explicit_submit", "desktop_attempt": task["attempt"]}
        finally:
            session["accepting"].clear()
            session["resources"].close()
            session["action_api"].close()

    def _session(self, handle, *, active=True):
        with self.lock:
            session = (self.handoff or {}).get("session")
            if not session or not secrets.compare_digest(session["handle"], handle):
                raise WorkerError("Unknown desktop task handle")
        if active and (not session["accepting"].is_set() or session["cancel"].is_set()):
            raise WorkerError("This desktop task no longer accepts operations")
        return session

    def resume_task(self, task_id, attempt):
        """Return a lost handoff only after a member confirms the previous conversation stopped."""
        with self.lock:
            handoff = self.handoff
            session = (handoff or {}).get("session")
            if (
                not session
                or not handoff.get("delivered")
                or session["task_id"] != task_id
                or session["attempt"] != attempt
            ):
                raise WorkerError("No matching delivered desktop task is available to recover")
        self._session(session["handle"])
        session["authorize"](
            session["handle"],
            "recovery",
            {
                "task_id": task_id,
                "attempt": attempt,
                "warning": "确认原桌面会话已停止，避免两个会话同时修改同一工作区。",
            },
        )
        self._session(session["handle"])
        return self._payload(session)

    def task_status(self, handle, wait_seconds=0):
        """Wait for a task to stop or submit, using the existing run cancellation signal."""
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 25:
            raise WorkerError("wait_seconds must be between 0 and 25")
        session = self._session(handle, active=False)
        deadline = time.monotonic() + wait_seconds
        while True:
            with session["state_lock"]:
                if session["cancel"].is_set():
                    state = "stopped"
                elif session["submitted"].is_set():
                    state = "stopped" if session.get("error") else "submitted"
                elif not session["accepting"].is_set():
                    state = "stopped"
                else:
                    state = "running"
            remaining = deadline - time.monotonic()
            if state != "running" or remaining <= 0:
                return {"state": state, "task_id": session["task_id"], "attempt": session["attempt"]}
            session["cancel"].wait(min(0.25, remaining))

    @staticmethod
    def _path(session, path):
        if not isinstance(path, str) or not path or "\0" in path:
            raise WorkerError("A workspace path is required")
        value = Path(path)
        return str(value if value.is_absolute() else session["workspace"] / value)

    def resource(self, handle, method, params):
        session = self._session(handle)
        if "path" in params:
            params["path"] = self._path(session, params["path"])
        if "cwd" in params:
            params["cwd"] = self._path(session, params["cwd"])
        return session["resources"].request(method, {"sessionId": handle, **params})

    def inspect_blender_exports(self, handle):
        session = self._session(handle)
        if session["capability"] != "blender":
            raise WorkerError("Blender inspection is available for Blender tasks")
        try:
            result = verify_exports(
                session["workspace"], self.config.get("blender_executable"), session["cancel"], run_process
            )
        except BlenderCheckError as exc:
            raise WorkerError(str(exc)) from exc
        self._session(handle)
        return result

    def propose_memory(self, handle, content, category):
        session = self._session(handle)
        return session["action_api"].request("POST", "/api/runs/memories", {"content": content, "category": category})

    def create_task(self, handle, title, prompt, capability, acceptance, dependencies, request_key):
        session = self._session(handle)
        return session["action_api"].request(
            "POST",
            "/api/runs/tasks",
            {
                "title": title,
                "prompt": prompt,
                "capability": capability,
                "kind": "worker",
                "acceptance": acceptance,
                "dependencies": dependencies,
                "client_request_id": request_key,
            },
        )

    def submit(self, handle, nonce, revision, summary):
        session = self._session(handle, active=False)
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 16000:
            raise WorkerError("A nonempty bounded summary with evidence is required")
        with session["state_lock"]:
            if not secrets.compare_digest(session["nonce"], nonce) or revision != session["revision"]:
                raise WorkerError("The task nonce or requirement revision does not match")
            if session["submitted"].is_set():
                if session.get("result") != summary:
                    raise WorkerError("This task was already submitted with different content")
                return {"state": "submitted", "message": "已提交，平台仍需捕获代码并安排独立验收。"}
            self._session(handle)
            # Verify the actual server lease before accepting a GUI-generated handoff.
            session["api"].request("POST", "/api/runs/heartbeat", {})
            self._session(handle)
            with session["resources"].lock:
                if any(t["process"].poll() is None for t in session["resources"].terminals.values()):
                    raise WorkerError("Wait for or stop all desktop commands before submitting")
            session["accepting"].clear()
            try:
                session["resources"].close()
                session["result"] = summary
            except Exception:
                session["error"] = "Desktop command cleanup failed; inspect the workspace"
                raise
            finally:
                session["submitted"].set()
        return {"state": "submitted", "message": "已提交，平台仍需捕获代码并安排独立验收。"}

    def close(self):
        with self.lock:
            self.closed = True
            handoff = self.handoff
            session = (handoff or {}).get("session")
            if session:
                session["accepting"].clear()
                session["cancel"].set()
        with self.worker.active_lock:
            for cancelled in self.worker.active.values():
                cancelled.set()
        if session:
            session["resources"].close()
        if handoff:
            handoff["done"].wait(10)
        self.worker.machine.close()


def build_server(bridge):
    mcp = FastMCP("pal_arena_desktop")

    @mcp.tool()
    def next_task() -> dict:
        """Claim one shared task for this active desktop conversation; never claims a personal task."""
        return bridge.next_task()

    @mcp.tool()
    def read_file(handle: str, path: str, line: int = 1, limit: int = 10000) -> dict:
        """Read UTF-8 text inside the task workspace; paths may be relative to that workspace."""
        return bridge.resource(handle, "fs/read_text_file", {"path": path, "line": line, "limit": limit})

    @mcp.tool()
    def resume_task(task_id: str, attempt: int) -> dict:
        """Recover a lost handoff after a member confirms the previous conversation has stopped."""
        return bridge.resume_task(task_id, attempt)

    @mcp.tool()
    def task_status(handle: str, wait_seconds: float = 0) -> dict:
        """Check or wait up to 25 seconds for this task to stop or be submitted."""
        return bridge.task_status(handle, wait_seconds)

    @mcp.tool()
    def inspect_blender_exports(handle: str) -> dict:
        """Open exported .blend projects with local Blender and return inspection details."""
        return bridge.inspect_blender_exports(handle)

    @mcp.tool()
    def write_file(handle: str, path: str, content: str) -> dict:
        """Write UTF-8 text inside the task workspace; execution settings require member approval."""
        return bridge.resource(handle, "fs/write_text_file", {"path": path, "content": content})

    @mcp.tool()
    def start_command(handle: str, command: str, args: list[str], cwd: str = ".") -> dict:
        """Request a workspace command; a platform member must approve each command."""
        return bridge.resource(handle, "terminal/create", {"command": command, "args": args, "cwd": cwd})

    @mcp.tool()
    def command_output(handle: str, terminal_id: str) -> dict:
        """Read bounded command output and, when available, its exit status."""
        return bridge.resource(handle, "terminal/output", {"terminalId": terminal_id})

    @mcp.tool()
    def stop_command(handle: str, terminal_id: str) -> dict:
        """Stop a workspace command and its supervised child processes."""
        return bridge.resource(handle, "terminal/release", {"terminalId": terminal_id})

    @mcp.tool()
    def propose_memory(handle: str, content: str, category: str = "context") -> dict:
        """Propose shared project memory for a member to confirm; do not store secrets."""
        return bridge.propose_memory(handle, content, category)

    @mcp.tool()
    def create_task(
        handle: str,
        title: str,
        prompt: str,
        capability: str = "code",
        acceptance: list[str] | None = None,
        dependencies: list[str] | None = None,
        request_key: str | None = None,
    ) -> dict:
        """Request a bounded child task; the platform enforces project policy and role limits."""
        return bridge.create_task(handle, title, prompt, capability, acceptance or [], dependencies or [], request_key)

    @mcp.tool()
    def submit_result(handle: str, nonce: str, requirement_revision: int, summary: str) -> dict:
        """Explicitly hand off completed work and verification evidence for independent review."""
        return bridge.submit(handle, nonce, requirement_revision, summary)

    return mcp


def mcp_config(config_path, token_path):
    args = ["internal-desktop-mcp"] if getattr(sys, "frozen", False) else ["-m", "arena.desktop_mcp"]
    args.extend(
        [
            "--config",
            str(Path(config_path).expanduser().resolve()),
            "--token-file",
            str(Path(token_path).expanduser().resolve()),
        ]
    )
    return {"mcpServers": {"pal-arena-desktop": {"command": sys.executable, "args": args, "env": {"PYTHONUTF8": "1"}}}}


def main():
    parser = argparse.ArgumentParser(description="User-invoked shared desktop MCP worker")
    parser.add_argument("--config", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--print-mcp-config", action="store_true")
    args = parser.parse_args()
    if args.print_mcp_config:
        print(json.dumps(mcp_config(args.config, args.token_file), ensure_ascii=False, indent=2))
        return
    token = Path(args.token_file).expanduser().read_text(encoding="utf-8-sig").strip()
    config = load_config(args.config)
    bridge = DesktopBridge(config, token)
    try:
        with workspace_lock(bridge.worker.root):
            build_server(bridge).run(transport="stdio")
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
