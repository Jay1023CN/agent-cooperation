"""Non-invasive local checks before connecting a Multica worker."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from .common import BridgeError, load_config


def preflight(config_path: str | None = None) -> dict:
    checks = {}
    path = config_path or os.getenv("AGENT_LINK_CONFIG")
    try:
        load_config(path)
        checks["configuration"] = {"ok": True, "source": "file" if path else "defaults"}
    except (OSError, ValueError, BridgeError):
        checks["configuration"] = {"ok": False, "reason": "配置文件不可读取或不是 JSON 对象"}

    # Multica launches its MCP server from a task directory, not the checkout.
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    try:
        with tempfile.TemporaryDirectory(prefix="pal-preflight-") as cwd:
            command = (
                [sys.executable, "internal-agent-link", "--help"]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-c", "import agent_link.server"]
            )
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                timeout=15,
                check=False,
            )
        checks["mcp_import"] = {"ok": result.returncode == 0}
    except (OSError, subprocess.TimeoutExpired):
        checks["mcp_import"] = {"ok": False}

    checks["commands"] = {
        name: {"found": bool(shutil.which(name))}
        for name in ("git", "multica", "qodercli", "codex", "claude", "gemini", "opencode")
    }
    return {
        "checks": checks,
        "note": "命令存在不代表已登录或可执行任务；桌面连接可另用 --check 检查。",
    }
