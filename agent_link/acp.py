from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
import uuid
from collections import deque

from .acp_client import ClientResources, external_process, stop_tree, without_multica_context
from .common import BridgeError, workspace_path
from .discovery import qoder_command
from .process_owner import spawn_owned


class ACP:
    kind = "acp-stdio"

    def __init__(self, name, config=None, *, process_env=None):
        self.name, self.config = name, config or {}
        self.process_env = process_env
        self.process = None
        self.info = None
        self.initialized = False
        self.pending = {}
        self.sessions_data = {}
        self.permissions_data = {}
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.start_lock = threading.Lock()
        self.submission_lock = threading.Lock()
        self.counter = 0
        self.closed = threading.Event()
        self.cleanup_error = None
        self.client_requests = threading.BoundedSemaphore(32)
        self.resources = ClientResources(self._session, self._authorize, environment=process_env)

    def connect(self):
        with self.start_lock:
            if self.process:
                if self.process.poll() is None and self.initialized and not self.closed.is_set():
                    return {"connected": True, "transport": self.kind, "agent": (self.info or {}).get("agentInfo")}
                raise BridgeError(
                    "This ACP process has ended or failed initialization; create a fresh adapter or restart its MCP server."
                )
            command = qoder_command(self.name, self.config)
            command, environment = external_process([*command, "--acp"], without_multica_context(self.process_env))
            self.closed.clear()
            self.resources = ClientResources(self._session, self._authorize, environment=self.process_env)
            self.process = spawn_owned(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=environment,
            )
            self.reader_thread = threading.Thread(target=self._reader, args=(self.process,), daemon=True)
            self.stderr_thread = threading.Thread(target=self._drain_stderr, args=(self.process,), daemon=True)
            self.exit_thread = threading.Thread(target=self._watch_exit, args=(self.process,), daemon=True)
            self.reader_thread.start()
            self.stderr_thread.start()
            self.exit_thread.start()
            try:
                self.info = self.call(
                    "initialize",
                    {
                        "protocolVersion": 1,
                        "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": True},
                        "clientInfo": {"name": "agent-link-mcp", "version": "0.1.0"},
                    },
                )
                if not isinstance(self.info, dict) or self.info.get("protocolVersion") != 1:
                    raise BridgeError("The agent did not negotiate the supported ACP protocol version.")
            except BaseException:
                self.close()
                raise
            self.initialized = True
            return {
                "connected": True,
                "transport": self.kind,
                "agent": self.info.get("agentInfo"),
                "note": "ACP agent process; this does not control an existing IDE chat.",
            }

    def _write(self, value):
        with self.write_lock:
            if not self.process or self.process.poll() is not None:
                raise BridgeError(f"{self.name} ACP process is not running.")
            self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def call(self, method, params, timeout=30):
        with self.lock:
            self.counter += 1
            identifier = self.counter
            item = {"event": threading.Event()}
            self.pending[identifier] = item
        try:
            message = {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
            if method == "session/prompt":
                # Serialize the first write with cancellation, not the whole
                # response wait: cancel must remain responsive during a turn.
                with self.submission_lock:
                    with self.lock:
                        state = self.sessions_data.get(params.get("sessionId"))
                        if not state or state.get("cancelled") or self.closed.is_set():
                            raise BridgeError("Prompt was cancelled before submission.")
                    self._write(message)
            else:
                self._write(message)
            if not item["event"].wait(timeout):
                raise BridgeError(f"{method} timed out; outcome unknown. Do not resend automatically.")
            response = item["response"]
            if "error" in response:
                error = response["error"]
                # Agent protocol errors can contain upstream credentials; code only.
                raise BridgeError(
                    f'{self.name} {method} failed (ACP {error.get("code")}). Check login and account eligibility.'
                )
            return response.get("result", {})
        finally:
            with self.lock:
                self.pending.pop(identifier, None)

    def _reader(self, process):
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    if "id" in message:
                        self._request(message)
                    elif message["method"] == "session/update":
                        self._update(message.get("params", {}))
                else:
                    with self.lock:
                        item = self.pending.get(message.get("id"))
                        if item:
                            item["response"] = message
                            item["event"].set()
        finally:
            self.closed.set()
            self.initialized = False
            with self.lock:
                for item in self.pending.values():
                    if not item["event"].is_set():
                        item["response"] = {"error": {"code": -32000}}
                        item["event"].set()
                for permission in self.permissions_data.values():
                    if "client_event" in permission:
                        permission["client_event"].set()
            failed = False
            for operation in (self.resources.close, lambda: stop_tree(process)):
                try:
                    operation()
                except Exception:
                    failed = True
            if failed:
                self._cleanup_failed()

    def _cleanup_failed(self):
        with self.lock:
            self.cleanup_error = "ACP process cleanup failed; inspect local processes before retrying."
            for state in self.sessions_data.values():
                if state.get("status") in ("starting", "running", "stopping", "finishing"):
                    state["error"] = self.cleanup_error

    def _watch_exit(self, process):
        try:
            process.wait()
            stop_tree(process)
        except Exception:
            self._cleanup_failed()

    @staticmethod
    def _drain_stderr(process):
        # Drain pipes to avoid blocking, but never log authentication or provider output.
        for _ in process.stderr:
            pass

    def _request(self, message):
        params = message.get("params", {})
        try:
            if not isinstance(params, dict):
                raise BridgeError("Invalid client request")
            self._session(params.get("sessionId"))
            ticket = self.resources.ticket(params.get("sessionId"))
        except (BridgeError, TypeError):
            with contextlib.suppress(BridgeError, OSError):
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32602, "message": "Unknown or inactive client session"},
                    }
                )
            return
        if message["method"] != "session/request_permission":
            methods = {
                "fs/read_text_file",
                "fs/write_text_file",
                "terminal/create",
                "terminal/output",
                "terminal/wait_for_exit",
                "terminal/kill",
                "terminal/release",
            }
            supported = message["method"] in methods
            if not supported or not self.client_requests.acquire(blocking=False):
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {
                            "code": -32000 if supported else -32601,
                            "message": "Client capability is busy" if supported else "Unsupported client capability",
                        },
                    }
                )
                return

            def respond():
                try:
                    result = self.resources.request(message["method"], params, ticket=ticket)
                    response = {"result": result}
                except Exception:
                    response = {
                        "error": {
                            "code": -32000,
                            "message": "Client request rejected or failed; check workspace scope and approval.",
                        }
                    }
                finally:
                    self.client_requests.release()
                with contextlib.suppress(BridgeError, OSError):
                    self._write({"jsonrpc": "2.0", "id": message["id"], **response})

            threading.Thread(target=respond, daemon=True).start()
            return
        options = params.get("options")
        if (
            not isinstance(options, list)
            or not 1 <= len(options) <= 32
            or any(not isinstance(option, dict) or not isinstance(option.get("optionId"), str) for option in options)
        ):
            self._write({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": {"outcome": "cancelled"}}})
            return
        key = str(uuid.uuid4())
        with self.lock:
            if not self.sessions_data[params["sessionId"]].get("accepting_requests"):
                self._write({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": {"outcome": "cancelled"}}})
                return
            self.permissions_data[key] = {
                "rpc_id": message["id"],
                "session_id": params.get("sessionId"),
                "tool": params.get("toolCall", {}),
                "options": params.get("options", []),
            }

    def _session(self, session_id):
        with self.lock:
            state = self.sessions_data.get(session_id)
            if not state or not state.get("accepting_requests") or state.get("cancelled") or self.closed.is_set():
                raise BridgeError("Unknown or cancelled ACP client session.")
            return state

    def _authorize(self, session_id, kind, details):
        self._session(session_id)
        policy = self.config.get("client_terminal_policy", "ask") if kind == "terminal" else "ask"
        if policy == "allow":
            return
        if policy != "ask":
            raise BridgeError("Client terminal policy denies this command.")
        key = str(uuid.uuid4())
        event = threading.Event()
        permission = {
            "session_id": session_id,
            "client_event": event,
            "allowed": False,
            "tool": {
                "title": "运行工作区命令" if kind == "terminal" else "修改执行配置文件",
                "kind": "execute" if kind == "terminal" else "edit",
                "rawInput": details,
            },
            "options": [
                {"optionId": "allow_once", "name": "允许本次操作", "kind": "allow_once"},
                {"optionId": "reject_once", "name": "拒绝本次操作", "kind": "reject_once"},
            ],
        }
        with self.lock:
            self.permissions_data[key] = permission
        try:
            deadline = time.monotonic() + 600
            while not event.wait(0.5):
                self._session(session_id)
                if time.monotonic() > deadline:
                    raise BridgeError("Client operation approval timed out.")
            self._session(session_id)
            if not permission["allowed"]:
                raise BridgeError("Client operation was not approved.")
        finally:
            with self.lock:
                self.permissions_data.pop(key, None)

    def _update(self, params):
        if not isinstance(params, dict) or not isinstance(params.get("sessionId"), str):
            return
        with self.lock:
            state = self.sessions_data.get(params.get("sessionId"))
            if state is None:
                return
            update = params.get("update", {})
            if not isinstance(update, dict):
                return
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text":
                    state["text"] += content.get("text", "")
                    if len(state["text"]) > 1_000_000:
                        state["text"] = state["text"][-1_000_000:]
                        state["error"] = "Agent output exceeded the bounded client limit."
            elif kind in ("tool_call", "tool_call_update"):
                state["events"].append(
                    {
                        "type": kind,
                        "title": update.get("title"),
                        "status": update.get("status"),
                        "tool_call_id": update.get("toolCallId"),
                    }
                )
            elif kind in ("current_mode_update", "config_option_update"):
                state["events"].append(update)

    def create(self, workspace, title="Agent Link", model=None):
        self.connect()
        path = str(workspace_path(workspace))
        result = self.call("session/new", {"cwd": path, "mcpServers": []})
        sid = result["sessionId"]
        with self.lock:
            self.sessions_data[sid] = {
                "id": sid,
                "title": title,
                "workspace": path,
                "status": "idle",
                "text": "",
                "events": deque(maxlen=200),
                "details": result,
                "done": threading.Event(),
            }
            self.sessions_data[sid]["done"].set()
        if model:
            self.call("session/set_model", {"sessionId": sid, "modelId": model})
        return {"id": sid, "details": result}

    def sessions(self, limit=30):
        with self.lock:
            return [
                {"id": s["id"], "title": s["title"], "workspace": s["workspace"], "status": s["status"]}
                for s in list(self.sessions_data.values())[-limit:]
            ]

    def models(self):
        with self.lock:
            return [s["details"].get("models", {}) for s in self.sessions_data.values()][-1:]

    def send(self, session_id, prompt):
        if not prompt.strip():
            raise BridgeError("prompt cannot be empty.")
        with self.lock:
            if self.closed.is_set() or not self.initialized:
                raise BridgeError(
                    "ACP connection is not initialized; create a fresh adapter or restart its MCP server."
                )
            state = self.sessions_data.get(session_id)
            if not state:
                raise BridgeError("Session is not loaded in this MCP process; create a session first.")
            if state["status"] in ("starting", "running", "waiting_permission", "stopping", "finishing"):
                raise BridgeError("Session is busy.")
            state.update(status="starting", text="", error=None, result=None, cancelled=False, accepting_requests=False)
            state["events"].clear()
            state["done"].clear()
        try:
            self.resources.begin(session_id)
        except BridgeError:
            with self.lock:
                state["status"] = "stopped"
                state["done"].set()
            raise
        with self.lock:
            start_cancelled = state.get("cancelled") or self.closed.is_set()
            if not start_cancelled:
                state.update(status="running", accepting_requests=True)
        if start_cancelled:
            self.resources.close(session_id)
            with self.lock:
                state["status"] = "stopped"
                state["done"].set()
            raise BridgeError("Session was cancelled before submission.")

        def run():
            final_status = "failed"
            try:
                result = self.call(
                    "session/prompt",
                    {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]},
                    timeout=600,
                )
                with self.lock:
                    state["result"] = result
                    final_status = (
                        "completed"
                        if result.get("stopReason") == "end_turn" and not state.get("cancelled")
                        else "stopped"
                    )
                    if not state["text"].strip() and not state["events"]:
                        final_status = "empty_response"
                    if state.get("error"):
                        final_status = "failed"
            except Exception as exc:
                with self.lock:
                    state["error"] = str(exc)
            finally:
                with self.lock:
                    state.update(status="finishing", accepting_requests=False)
                    stale = [key for key, p in self.permissions_data.items() if p["session_id"] == session_id]
                    for key in stale:
                        permission = self.permissions_data.pop(key)
                        if "client_event" in permission:
                            permission["client_event"].set()
                            continue
                        try:
                            self._write(
                                {
                                    "jsonrpc": "2.0",
                                    "id": permission["rpc_id"],
                                    "result": {"outcome": {"outcome": "cancelled"}},
                                }
                            )
                        except (BridgeError, OSError):
                            pass
                try:
                    # No owned terminal or pending write may outlive a completed turn.
                    self.resources.close(session_id)
                except Exception:
                    final_status = "failed"
                    state["error"] = "ACP client resource cleanup failed; inspect the local processes."
                finally:
                    with self.lock:
                        state["status"] = "stopped" if state.get("cancelled") else final_status
                        state["done"].set()

        threading.Thread(target=run, daemon=True).start()
        return {"submitted": True, "session_id": session_id}

    def read(self, session_id, limit=20):
        with self.lock:
            state = self.sessions_data.get(session_id)
            if not state:
                raise BridgeError("Unknown session in this MCP process.")
            permissions = self.permissions(session_id)
            return {
                "session_id": session_id,
                "status": "waiting_permission" if permissions else state["status"],
                "text": state["text"],
                "error": state.get("error"),
                "events": list(state["events"])[-limit:],
                "result": state.get("result"),
                "permissions": permissions,
            }

    def wait(self, session_id, seconds=30):
        with self.lock:
            if session_id not in self.sessions_data:
                raise BridgeError("Unknown session.")
            done = self.sessions_data[session_id]["done"]
        # Return quickly if a permission request needs attention.
        for _ in range(max(1, min(int(seconds), 45))):
            if done.wait(1) or self.permissions(session_id):
                break
        return self.read(session_id)

    def permissions(self, session_id):
        with self.lock:
            return [
                {"id": k, "session_id": p["session_id"], "tool": p["tool"], "options": p["options"]}
                for k, p in self.permissions_data.items()
                if p["session_id"] == session_id
            ]

    def reply(self, permission_id, option_id=None):
        with self.lock:
            permission = self.permissions_data.get(permission_id)
            if not permission:
                raise BridgeError("Permission request expired or not found.")
            if option_id and option_id not in [o.get("optionId") for o in permission["options"]]:
                raise BridgeError("Unknown permission option.")
            outcome = {"outcome": "selected", "optionId": option_id} if option_id else {"outcome": "cancelled"}
            if "client_event" in permission:
                permission["allowed"] = option_id == "allow_once"
                permission["client_event"].set()
            else:
                self._write({"jsonrpc": "2.0", "id": permission["rpc_id"], "result": {"outcome": outcome}})
            self.permissions_data.pop(permission_id)
            return {"replied": True}

    def cancel(self, session_id):
        with self.submission_lock:
            with self.lock:
                state = self.sessions_data.get(session_id)
                if not state:
                    raise BridgeError("Unknown session.")
                if state["done"].is_set():
                    return {"cancel_requested": False, "session_id": session_id, "status": state["status"]}
                state["cancelled"] = True
                state["status"] = "stopping"
                state["accepting_requests"] = False
                for key in [
                    key for key, permission in self.permissions_data.items() if permission["session_id"] == session_id
                ]:
                    permission = self.permissions_data.pop(key)
                    if "client_event" in permission:
                        permission["client_event"].set()
                    else:
                        with contextlib.suppress(BridgeError, OSError):
                            self._write(
                                {
                                    "jsonrpc": "2.0",
                                    "id": permission["rpc_id"],
                                    "result": {"outcome": {"outcome": "cancelled"}},
                                }
                            )
            with contextlib.suppress(BridgeError, OSError):
                self._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
        self.resources.close(session_id)
        return {"cancel_requested": True, "session_id": session_id}

    def close(self):
        with self.submission_lock:
            self.closed.set()
            with self.lock:
                sessions = list(self.sessions_data)
                for permission in self.permissions_data.values():
                    if "client_event" in permission:
                        permission["client_event"].set()
            for session_id in sessions:
                with contextlib.suppress(BridgeError, OSError):
                    self._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
        failed = False
        operations = [self.resources.close]
        if self.process:
            operations.append(lambda: stop_tree(self.process))
        for operation in operations:
            try:
                operation()
            except Exception:
                failed = True
        if failed:
            self._cleanup_failed()
        if self.process:
            for thread in (
                getattr(self, "reader_thread", None),
                getattr(self, "stderr_thread", None),
                getattr(self, "exit_thread", None),
            ):
                if thread:
                    thread.join(timeout=2)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream:
                    stream.close()
        if self.cleanup_error:
            raise BridgeError(self.cleanup_error)
