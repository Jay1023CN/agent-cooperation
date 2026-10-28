"""Make the workspace reachable from the member's other computers through Tailscale Serve.

This is the one step that changes Tailscale settings, so it only runs when the owner asks for it
(`pal-arena serve --share` or `pal-arena share`). The center keeps listening on 127.0.0.1;
Tailscale terminates HTTPS on the tailnet name and forwards to that local port.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path

from agent_link.device_setup import _run, find_tailscale

SHARE_FILE = "share.json"
ENABLE_LINK = re.compile(r"https://login\.tailscale\.com/\S+")


def _cli_environment(cli: str) -> dict[str, str]:
    env = os.environ.copy()
    if platform.system() == "Darwin" and cli.endswith("/Tailscale.app/Contents/MacOS/Tailscale"):
        env["TAILSCALE_BE_CLI"] = "1"
    return env


def _status(cli: str) -> dict:
    completed = _run(cli, ["status", "--json"])
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or "tailscale status failed")
    status = json.loads(completed.stdout)
    if not isinstance(status, dict):
        raise ValueError("unexpected tailscale status")
    return status


def share(port: int, data_dir: Path | None = None) -> dict:
    """Run `tailscale serve` for the local port and return what other computers should use."""
    result = {"state": "failed", "url": None, "message": "", "action_url": None, "checked": time.time()}
    cli = find_tailscale()
    if not cli:
        result.update(
            state="not_installed",
            message="这台电脑还没有安装 Tailscale。",
            action_url="https://tailscale.com/download",
        )
        return _save(result, data_dir)
    try:
        status = _status(cli)
    except (OSError, subprocess.SubprocessError, ValueError):
        result.update(state="unavailable", message="Tailscale 没有响应。打开 Tailscale 应用后再试。")
        return _save(result, data_dir)
    if status.get("BackendState") != "Running":
        result.update(state="login_required", message="打开 Tailscale 应用并登录，状态变成已连接后再试。")
        return _save(result, data_dir)
    name = str((status.get("Self") or {}).get("DNSName") or "").rstrip(".")
    if not name:
        result.update(message="Tailscale 没有给这台电脑分配网络名称，检查管理后台的 MagicDNS 是否开启。")
        return _save(result, data_dir)
    try:
        # Serve asks for HTTPS certificates to be enabled once per tailnet; it then prints a link.
        completed = subprocess.run(
            [cli, "serve", "--bg", f"http://127.0.0.1:{int(port)}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
            check=False,
            env=_cli_environment(cli),
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        ok = completed.returncode == 0
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) else ""
        ok = False
    except OSError:
        output, ok = "", False
    link = ENABLE_LINK.search(output)
    if not ok:
        if link:
            result.update(
                state="needs_enable",
                message="你的 Tailscale 网络还没开启 HTTPS 共享。打开下面的链接点一次「启用」，再运行一次这个命令。",
                action_url=link.group(0),
            )
        else:
            result.update(message="tailscale serve 没有成功：" + (output.strip()[-300:] or "没有输出"))
        return _save(result, data_dir)
    result.update(state="shared", url=f"https://{name}", message="其他电脑和伙伴用这个地址访问工作台、连接执行端。")
    return _save(result, data_dir)


def _save(result: dict, data_dir: Path | None) -> dict:
    if data_dir:
        path = Path(data_dir) / SHARE_FILE
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def saved(data_dir: Path) -> dict | None:
    try:
        value = json.loads((Path(data_dir) / SHARE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def describe(result: dict) -> str:
    lines = {
        "shared": f"✓ 已共享：{result['url']}",
        "not_installed": "✗ 没有找到 Tailscale",
        "unavailable": "✗ Tailscale 没有响应",
        "login_required": "✗ Tailscale 还没登录或没连上",
        "needs_enable": "! 需要在 Tailscale 里点一次「启用 HTTPS」",
    }
    text = [lines.get(result["state"], "✗ 共享没有成功"), "  " + result["message"]]
    if result.get("action_url"):
        text.append("  " + result["action_url"])
    return "\n".join(text)
