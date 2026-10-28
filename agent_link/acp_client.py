"""ACP v1 client file and terminal resources for a registered workspace.

Terminal commands run as the current OS user; workspace checks are not an OS sandbox.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from .common import BridgeError
from .process_owner import spawn_owned, stop_owned

MAX_TEXT_BYTES = 2_000_000
MAX_OUTPUT_BYTES = 1_000_000
CONTROL_DIRECTORIES = {".claude", ".qoder", ".workbuddy", ".vscode", ".cursor"}
CONTROL_FILES = {"AGENTS.md", "CLAUDE.md", ".mcp.json", "mcp.json"}
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")


def without_multica_context(environment=None):
    """Do not give a delegated local client its host Multica task identity."""
    return {
        key: value
        for key, value in (os.environ if environment is None else environment).items()
        if not key.upper().startswith("MULTICA_")
    }


def external_process(command, environment=None):
    command = list(command)
    environment = dict(os.environ if environment is None else environment)
    if getattr(sys, "frozen", False):
        for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH"):
            original = environment.pop(key + "_ORIG", None)
            if original is None:
                environment.pop(key, None)
            else:
                environment[key] = original
        if sys.platform == "win32":
            command = [sys.executable, "internal-exec", *command]
    return command, environment


def stop_tree(process):
    stop_owned(process)


class ClientResources:
    def __init__(self, session, authorize, *, environment=None):
        self.session = session
        self.authorize = authorize
        self.environment = environment
        self.terminals = {}
        self.lock = threading.RLock()
        self.closed = False
        self.generations = {}
        self.closed_sessions = set()

    def begin(self, session_id):
        with self.lock:
            if self.closed:
                raise BridgeError("Client resources are closed.")
            self.generations[session_id] = self.generations.get(session_id, 0) + 1
            self.closed_sessions.discard(session_id)

    def ticket(self, session_id):
        with self.lock:
            if self.closed or session_id in self.closed_sessions:
                raise BridgeError("Client resources for this turn are closed.")
            return self.generations.get(session_id, 0)

    def _check_ticket(self, session_id, ticket):
        if self.ticket(session_id) != ticket:
            raise BridgeError("Client request belongs to an earlier prompt turn.")

    def _workspace(self, session_id):
        if self.closed or session_id in self.closed_sessions:
            raise BridgeError("Client resources are closed.")
        return Path(self.session(session_id)["workspace"]).resolve()

    def _path(self, session_id, value, *, directory=False):
        root = self._workspace(session_id)
        if not isinstance(value, str) or not value or "\0" in value:
            raise BridgeError("A valid absolute workspace path is required.")
        path = Path(value)
        if not path.is_absolute():
            raise BridgeError("ACP paths must be absolute.")
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise BridgeError("Path is outside the registered session workspace.")
        if any(part.casefold() == ".git" for part in (*resolved.relative_to(root).parts, *path.parts)):
            raise BridgeError("Direct access to Git control files is disabled.")
        if directory and not resolved.is_dir():
            raise BridgeError("Terminal working directory must exist.")
        return root, resolved

    @staticmethod
    def _integer(value, name, low, high):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise BridgeError(f"{name} is outside the supported range.")
        return value

    def request(self, method, params, *, ticket=None):
        if not isinstance(params, dict):
            raise BridgeError("Invalid ACP client parameters.")
        session_id = params.get("sessionId")
        if ticket is None:
            ticket = self.ticket(session_id)
        self._check_ticket(session_id, ticket)
        self._workspace(session_id)
        if method.startswith("fs/"):
            root, path = self._path(session_id, params.get("path"))
            if method == "fs/read_text_file":
                if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
                    raise BridgeError("Text file is missing or exceeds the client read limit.")
                start = self._integer(params.get("line", 1), "line", 1, MAX_TEXT_BYTES)
                limit = self._integer(params.get("limit", MAX_TEXT_BYTES), "limit", 0, MAX_TEXT_BYTES)
                with path.open("rb") as source:
                    data = source.read(MAX_TEXT_BYTES + 1)
                if len(data) > MAX_TEXT_BYTES:
                    raise BridgeError("Text file grew beyond the client read limit.")
                content = data.decode("utf-8")
                return {"content": "".join(content.splitlines(keepends=True)[start - 1 : start - 1 + limit])}
            if method == "fs/write_text_file":
                content = params.get("content")
                if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_TEXT_BYTES:
                    raise BridgeError("Text content exceeds the client write limit.")
                relative = path.relative_to(root)
                if (
                    any(
                        part.casefold() in CONTROL_DIRECTORIES
                        for part in (*relative.parts, *Path(params["path"]).parts)
                    )
                    or path.name.casefold() in {name.casefold() for name in CONTROL_FILES}
                    or Path(params["path"]).name.casefold() in {name.casefold() for name in CONTROL_FILES}
                ):
                    self.authorize(session_id, "write_control_file", {"path": str(path)})
                # Complete the filesystem mutation before close() returns. Recheck after
                # human approval, which may have outlived cancellation or a prompt turn.
                with self.lock:
                    self._check_ticket(session_id, ticket)
                    self._path(session_id, str(path))
                    if path.exists() and not path.is_file():
                        raise BridgeError("Text destination is not a regular file.")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            "w", encoding="utf-8", newline="", dir=path.parent, delete=False
                        ) as out:
                            temporary = Path(out.name)
                            out.write(content)
                            out.flush()
                            os.fsync(out.fileno())
                        if path.exists():
                            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
                        temporary.replace(path)
                    finally:
                        if temporary:
                            temporary.unlink(missing_ok=True)
                return {}
            raise BridgeError("Unsupported ACP filesystem method.")
        if method == "terminal/create":
            return self._create_terminal(session_id, params, ticket)
        with self.lock:
            terminal = self.terminals.get(params.get("terminalId"))
            if not terminal or terminal["session_id"] != session_id:
                raise BridgeError("Unknown terminal for this session.")
        if method == "terminal/output":
            return self._output(terminal)
        if method == "terminal/wait_for_exit":
            terminal["done"].wait()
            if terminal.get("cleanup_error"):
                raise BridgeError("Terminal cleanup failed; its outcome is unknown.")
            return self._exit_status(terminal["process"])
        if method in ("terminal/kill", "terminal/release"):
            stop_tree(terminal["process"])
            if not terminal["done"].wait(3) or terminal.get("cleanup_error"):
                raise BridgeError("Terminal cleanup failed; its outcome is unknown.")
            if method == "terminal/release":
                with self.lock:
                    self.terminals.pop(params["terminalId"], None)
            return {}
        raise BridgeError("Unsupported ACP terminal method.")

    def _create_terminal(self, session_id, params, ticket):
        root = self._workspace(session_id)
        _, cwd = self._path(session_id, params.get("cwd", str(root)), directory=True)
        command, arguments = params.get("command"), params.get("args", [])
        if (
            not isinstance(command, str)
            or not command
            or "\0" in command
            or not isinstance(arguments, list)
            or len(arguments) > 256
            or any(not isinstance(arg, str) or "\0" in arg for arg in arguments)
        ):
            raise BridgeError("Terminal command must be a program with a bounded argv array.")
        if sys.platform == "win32":
            # Windows can invoke batch files through cmd.exe even with shell=False,
            # where argv containing metacharacters is no longer passed literally.
            executable = shutil.which(command) or command
            if Path(executable).suffix.casefold() in {".cmd", ".bat"}:
                raise BridgeError("Windows batch entrypoints are not supported; use a native executable.")
        variables = params.get("env", [])
        if not isinstance(variables, list) or len(variables) > 100:
            raise BridgeError("Invalid terminal environment.")
        additions = {}
        for item in variables:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["name"])
                or item["name"].upper().startswith(("ARENA_", "MULTICA_"))
                or not isinstance(item.get("value"), str)
                or "\0" in item["value"]
                or len(item["value"]) > 8000
            ):
                raise BridgeError("Invalid or reserved terminal environment variable.")
            additions[item["name"]] = item["value"]
        limit = self._integer(params.get("outputByteLimit", MAX_OUTPUT_BYTES), "outputByteLimit", 1, MAX_OUTPUT_BYTES)
        self.authorize(
            session_id, "terminal", {"command": command, "args": arguments, "cwd": str(cwd), "env": additions}
        )
        self._path(session_id, str(cwd), directory=True)
        env = {
            key: value
            for key, value in without_multica_context(self.environment).items()
            if not key.upper().startswith("ARENA_")
        }
        env.update(additions)
        argv, env = external_process([command, *arguments], env)
        with self.lock:
            self._check_ticket(session_id, ticket)
            if self.closed or len(self.terminals) >= 8:
                raise BridgeError("Release an existing terminal before creating another.")
            process = spawn_owned(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            identifier = uuid.uuid4().hex
            terminal = {
                "session_id": session_id,
                "process": process,
                "bytes": b"",
                "truncated": False,
                "limit": limit,
                "done": threading.Event(),
                "output_done": threading.Event(),
                "cleanup_error": False,
            }
            self.terminals[identifier] = terminal

        def consume():
            try:
                while chunk := os.read(process.stdout.fileno(), 4096):
                    with self.lock:
                        buffer = terminal["bytes"] + chunk
                        terminal["truncated"] |= len(buffer) > limit
                        terminal["bytes"] = buffer[-limit:]
                process.wait()
            finally:
                process.stdout.close()
                terminal["output_done"].set()

        threading.Thread(target=consume, daemon=True).start()

        def reap_children():
            try:
                process.wait()
                stop_tree(process)
            except Exception:
                terminal["cleanup_error"] = True
            finally:
                if not terminal["output_done"].wait(3):
                    terminal["cleanup_error"] = True
                terminal["done"].set()

        threading.Thread(target=reap_children, daemon=True).start()
        return {"terminalId": identifier}

    @staticmethod
    def _exit_status(process):
        code = process.poll()
        if code is None:
            return None
        if code < 0 and os.name != "nt":
            try:
                name = signal.Signals(-code).name
            except ValueError:
                name = str(-code)
            return {"exitCode": None, "signal": name}
        return {"exitCode": code, "signal": None}

    def _output(self, terminal):
        with self.lock:
            if terminal.get("cleanup_error"):
                raise BridgeError("Terminal cleanup failed; its outcome is unknown.")
            output = terminal["bytes"].decode("utf-8", errors="ignore")
            output = ANSI.sub("", output)
            output = "".join(c for c in output if c in "\n\r\t" or ord(c) >= 32)
            result = {"output": output, "truncated": terminal["truncated"]}
            status = self._exit_status(terminal["process"])
            if status is not None and terminal["done"].is_set():
                result["exitStatus"] = status
            return result

    def close(self, session_id=None):
        with self.lock:
            if session_id is None:
                self.closed = True
            else:
                self.closed_sessions.add(session_id)
                self.generations[session_id] = self.generations.get(session_id, 0) + 1
            ids = [
                key for key, value in self.terminals.items() if session_id is None or value["session_id"] == session_id
            ]
            terminals = [self.terminals.pop(key) for key in ids]
        failed = False
        for terminal in terminals:
            try:
                stop_tree(terminal["process"])
                if not terminal["done"].wait(3) or terminal.get("cleanup_error"):
                    failed = True
            except Exception:
                failed = True
        if failed:
            raise BridgeError("Terminal cleanup failed; inspect the local processes.")
