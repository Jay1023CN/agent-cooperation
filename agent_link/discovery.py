from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .common import BridgeError

ACP_PROVIDERS = frozenset({"qoder", "qodercn", "dsh"})


def acp_command(name, config):
    if name != "dsh":
        return qoder_command(name, config)
    command = config.get("command")
    if command is not None:
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise BridgeError("dsh.command must be a nonempty string array.")
        return command
    executable = shutil.which("dsh")
    if executable and not executable.lower().endswith((".cmd", ".bat", ".ps1")):
        return [executable]
    node = shutil.which("node")
    if node:
        prefixes = [
            Path(node).resolve().parent / "node_modules",
            Path(node).resolve().parent.parent / "lib/node_modules",
        ]
        if os.getenv("APPDATA"):
            prefixes.insert(0, Path(os.environ["APPDATA"]) / "npm/node_modules")
        if executable:
            prefixes.insert(0, Path(executable).parent / "node_modules")
        for prefix in prefixes:
            root = (prefix / "@deepseek-ai/dsh").resolve()
            manifest = root / "package.json"
            if not manifest.is_file():
                continue
            bins = json.loads(manifest.read_text(encoding="utf-8")).get("bin", {})
            relative = bins.get("dsh") if isinstance(bins, dict) else bins
            if isinstance(relative, str):
                script = (root / relative).resolve()
                if script.is_relative_to(root) and script.is_file():
                    return [node, str(script)]
    raise BridgeError("DSH executable not found. Install DeepSeek Harness or configure dsh.command.")


def acp_launch_command(name, config):
    command = acp_command(name, config)
    if name != "dsh":
        return [*command, "--acp"]
    profile = config.get("profile", "acp")
    if not isinstance(profile, str) or not profile.strip() or profile.startswith("-"):
        raise BridgeError("dsh.profile must be a nonempty ACP profile name.")
    return [*command, "--profile", profile]


def opencode_service(config):
    if config.get("url"):
        return {"url": config["url"], "password": os.getenv(config.get("password_env", "OPENCODE_SERVER_PASSWORD"), "")}
    state = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    candidates = [state / "opencode/service.json"]
    if os.getenv("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "opencode/service.json")
    for path in candidates:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("url"):
                return data
    raise BridgeError(
        "OpenCode background service not found. Start OpenCode, or configure opencode.url and password_env."
    )


def qoder_command(name, config):
    if config.get("command"):
        command = config["command"]
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise BridgeError(f"{name}.command must be a nonempty string array.")
        return command
    cn = name == "qodercn"
    cli = "qoderclicn" if cn else "qodercli"
    folder = ".qoder-cn" if cn else ".qoder"
    for executable in (cli, "qodercn" if cn else "qoder"):
        path = shutil.which(executable)
        # Prefer native executables. Windows dispatch shims may open the IDE instead.
        if path and (sys.platform != "win32" or path.lower().endswith(".exe")):
            return [path]
    native = Path.home() / folder / "bin" / cli / (cli + (".exe" if sys.platform == "win32" else ""))
    if native.is_file():
        return [str(native)]
    node = shutil.which("node")
    package = "@qodercn-ai/qoderclicn" if cn else "@qoder-ai/qodercli"
    prefixes = []
    if os.getenv("APPDATA"):
        prefixes.append(Path(os.environ["APPDATA"]) / "npm/node_modules")
    if node:
        prefixes.extend(
            [Path(node).resolve().parent / "node_modules", Path(node).resolve().parent.parent / "lib/node_modules"]
        )
        for prefix in prefixes:
            root = prefix / package
            if not (root / "package.json").is_file():
                continue
            data = json.loads((root / "package.json").read_text(encoding="utf-8"))
            bins = data.get("bin", {})
            script = bins.get(cli) if isinstance(bins, dict) else bins
            if script and (root / script).is_file():
                return [node, str(root / script)]
    raise BridgeError(f"{name} CLI not found. Install and sign in with the official CLI, or configure {name}.command.")


def antigravity_services():
    import psutil

    result = []
    for process in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            info = process.info
            if "language_server" not in (info["name"] or "").lower():
                continue
            if "antigravity" not in (info["exe"] or "").lower():
                continue
            args = info["cmdline"] or []
            token = None
            for index, arg in enumerate(args):
                if arg == "--csrf_token" and index + 1 < len(args):
                    token = args[index + 1]
                elif arg.startswith("--csrf_token="):
                    token = arg.split("=", 1)[1]
            if not token:
                continue
            ports = {c.laddr.port for c in process.net_connections("tcp") if c.status == psutil.CONN_LISTEN}
            for port in ports:
                result.append({"url": f"https://127.0.0.1:{port}", "token": token, "pid": process.pid})
        except (psutil.Error, OSError):
            continue
    return result
