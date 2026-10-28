"""Read-only network checks for the computer running the setup helper."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def find_tailscale() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    candidates = []
    if os.name == "nt":
        candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe")
    elif platform.system() == "Darwin":
        candidates.extend(
            [Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"), Path("/usr/local/bin/tailscale")]
        )
    return next((str(path) for path in candidates if path.is_file()), None)


def _name(value: object) -> str | None:
    return value.rstrip(".").lower() if isinstance(value, str) and value else None


def _action(state: str) -> dict:
    actions = {
        "not_installed": ("install", "安装 Tailscale", "https://tailscale.com/download"),
        "login_required": ("sign_in", "打开 Tailscale 并登录", None),
        "approval_required": ("approve", "等待网络管理员批准这台电脑", None),
        "starting": ("wait", "正在连接，请稍后刷新", None),
        "disconnected": ("connect", "打开 Tailscale 并连接网络", None),
        "wrong_network": ("switch_network", "切换到项目所在的网络", None),
        "network_unverified": ("check_network", "无法确认是否加入项目网络，请重试", None),
        "unavailable": ("check_app", "打开 Tailscale 后重试", None),
        "connected": ("continue", "网络已连接", None),
    }
    kind, label, url = actions[state]
    return {"kind": kind, "label": label, "url": url}


def _run(cli: str, args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if platform.system() == "Darwin" and cli.endswith("/Tailscale.app/Contents/MacOS/Tailscale"):
        env["TAILSCALE_BE_CLI"] = "1"
    return subprocess.run(
        [cli, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        env=env,
        check=False,
    )


def _center_url(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("invalid center URL")
    except ValueError:
        raise ValueError("Center URL must be an HTTPS origin") from None
    return value.rstrip("/"), _name(parsed.hostname)


def _service_state(origin: str) -> str:
    try:
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            response = client.get(origin + "/readyz")
        if response.status_code == 503:
            return "not_ready"
        if response.status_code != 200:
            return "unavailable"
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("checks"), dict):
            return "unavailable"
        checks = payload["checks"]
        return (
            "ready"
            if payload.get("status") == "ok" and checks.get("db") == checks.get("migrations") == "ok"
            else "not_ready"
        )
    except (httpx.HTTPError, ValueError):
        return "unavailable"


def inspect_device(
    expected_tailnet: str | None = None, center: str | None = None, center_url: str | None = None
) -> dict:
    """Inspect this host only. Never expose raw CLI output, peers or credentials."""
    service_origin, service_host = _center_url(center_url)
    if service_host and center and service_host != _name(center):
        raise ValueError("Center URL and device address must match")
    center = center or service_host
    result = {
        "version": 1,
        "scope": "local_device",
        "device": {"name": socket.gethostname(), "os": platform.system()},
        "network": {"state": "not_installed", "tailnet": None, "address": None, "matches_expected": None},
        "center": {
            "state": "not_configured" if not center else "not_checked",
            "service": "not_configured" if not service_origin else "not_checked",
        },
    }
    cli = find_tailscale()
    if not cli:
        result["next_action"] = _action("not_installed")
        return result
    try:
        completed = _run(cli, ["status", "--json"])
        if completed.returncode:
            raise ValueError("status unavailable")
        status = json.loads(completed.stdout)
        if not isinstance(status, dict):
            raise ValueError("invalid status")
    except (OSError, subprocess.SubprocessError, ValueError):
        result["network"]["state"] = "unavailable"
        result["next_action"] = _action("unavailable")
        return result

    self_node = status.get("Self") if isinstance(status.get("Self"), dict) else {}
    tailnet = status.get("CurrentTailnet") if isinstance(status.get("CurrentTailnet"), dict) else {}
    network = result["network"]
    network["tailnet"] = _name(tailnet.get("Name"))
    addresses = self_node.get("TailscaleIPs") or status.get("TailscaleIPs") or []
    if isinstance(addresses, list):
        network["address"] = next((address for address in addresses if isinstance(address, str)), None)
    expected = _name(expected_tailnet)
    if expected and network["tailnet"]:
        network["matches_expected"] = expected == network["tailnet"]
    backend = status.get("BackendState")
    states = {
        "NeedsLogin": "login_required",
        "NeedsMachineAuth": "approval_required",
        "Starting": "starting",
        "Stopped": "disconnected",
        "NoState": "starting",
    }
    if backend == "Running":
        if not network["address"] or self_node.get("Online") is False:
            state = "disconnected"
        elif network["matches_expected"] is False:
            state = "wrong_network"
        elif expected and network["matches_expected"] is None:
            state = "network_unverified"
        else:
            state = "connected"
    else:
        state = states.get(backend, "unavailable")
    network["state"] = state
    if center and state == "connected":
        peers = status.get("Peer") if isinstance(status.get("Peer"), dict) else {}
        target = _name(center)
        peer = next(
            (
                item
                for item in peers.values()
                if isinstance(item, dict)
                and target
                and target
                in {
                    _name(item.get("DNSName")),
                    *(_name(ip) for ip in (item.get("TailscaleIPs") or []) if isinstance(ip, str)),
                }
            ),
            None,
        )
        if not peer:
            result["center"]["state"] = "not_found"
        else:
            # Only a peer returned by the local Tailscale client can be probed.
            try:
                ping = _run(cli, ["ping", "--c", "1", "--timeout", "4s", center])
                result["center"]["state"] = "reachable" if ping.returncode == 0 else "unreachable"
            except (OSError, subprocess.SubprocessError):
                result["center"]["state"] = "unreachable"
            if service_origin:
                result["center"]["service"] = _service_state(service_origin)
    result["next_action"] = _action(state)
    return result
