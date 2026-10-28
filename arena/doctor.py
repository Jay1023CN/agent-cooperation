"""Read-only installation diagnostics, with no model requests or credential output."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import sys
from pathlib import Path


def diagnose(config_path=None, *, probe_transport=True):
    checks = []

    def add(name, state, detail):
        checks.append({"name": name, "state": state, "detail": detail})

    add("runtime", "ok", "Bundled Python runtime" if getattr(sys, "frozen", False) else platform.python_version())
    for package in ("fastapi", "uvicorn", "httpx", "mcp", "psutil"):
        try:
            add(package, "ok", importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            add(package, "error", "Missing runtime package; reinstall the platform bundle")
    for name in ("git", "tailscale", "blender"):
        found = bool(shutil.which(name))
        note = {
            "git": "Required for code workspaces",
            "tailscale": "Optional for access between your computers",
            "blender": "Required only on computers assigned Blender work",
        }[name]
        add(name, "ok" if found else "warning", ("Executable found. " if found else "Not on PATH. ") + note)
    agents = []
    if config_path:
        try:
            from .worker import inspect_agent, load_config

            config = load_config(config_path)
            add("worker_config", "ok", "Configuration parsed; credentials are not included in this report")
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            # Configuration values or exception messages may contain user credentials.
            add("worker_config", "error", "Configuration could not be loaded; check JSON format and required fields")
            config = None
        if config:
            for agent in config["agents"]:
                readiness = inspect_agent(agent, probe_transport=probe_transport)
                agents.append(
                    {
                        "key": agent["key"],
                        "name": agent["name"],
                        "model": agent.get("model"),
                        "mode": agent["mode"],
                        "enabled": agent.get("enabled", True),
                        **readiness,
                    }
                )
            if not any(a["dispatchable"] and a["enabled"] for a in agents):
                add("dispatch", "warning", "No configured agent currently passes local prerequisites")
            else:
                add(
                    "dispatch",
                    "ok",
                    "Local prerequisites passed; login, subscription and task execution still need validation",
                )
            if any(a["account_readiness"] != "verified" for a in agents if a["enabled"]):
                add(
                    "accounts",
                    "unknown",
                    "Client login, model entitlement and subscription quota require an actual task check",
                )
            root = Path(config["workspace_root"]).expanduser().resolve()
            while not root.exists() and root != root.parent:
                root = root.parent
            free = shutil.disk_usage(root).free
            writable = os.access(root, os.W_OK)
            add(
                "workspace_storage",
                "ok" if writable and free >= config["storage_limit_bytes"] else "warning",
                f"Free bytes: {free}; configured workspace budget: {config['storage_limit_bytes']}; writable: {writable}",
            )
            if probe_transport:
                import httpx

                try:
                    response = httpx.get(
                        config["server"] + "/health", timeout=5, follow_redirects=False, trust_env=False
                    )
                    body = response.json()
                    healthy = (
                        response.status_code == 200
                        and isinstance(body, dict)
                        and body.get("status") == "ok"
                        and body.get("name") == "PAL Arena"
                    )
                except (httpx.HTTPError, ValueError):
                    healthy = False
                add(
                    "server",
                    "ok" if healthy else "warning",
                    (
                        "Control server is reachable"
                        if healthy
                        else "Control server health was not confirmed; check address and Tailscale"
                    ),
                )
            else:
                add("server", "unknown", "Network check skipped")
    else:
        add("worker_config", "unknown", "Use --config worker.json to inspect this computer’s configured agents")
    return {
        "schema_version": 1,
        "system": platform.system(),
        "architecture": platform.machine(),
        "status": (
            "error"
            if any(c["state"] == "error" for c in checks)
            else "needs_attention" if any(c["state"] in ("warning", "unknown") for c in checks) else "ok"
        ),
        "checks": checks,
        "agents": agents,
        "limits": [
            "No task or model prompt was sent.",
            "Account login, model entitlement and remaining subscription quota are unverified.",
            "A successful local check does not replace testing an actual task in the installed client.",
        ],
    }


def render(report, *, json_output=False):
    if json_output:
        return json.dumps(report, ensure_ascii=False, indent=2)
    lines = [f"PAL Arena installation check: {report['status']}", f"{report['system']} / {report['architecture']}"]
    lines.extend(f"[{c['state']}] {c['name']}: {c['detail']}" for c in report["checks"])
    lines.extend(
        f"[{'ready to try' if a['dispatchable'] else 'unavailable'}] {a['name']}: {a['reason']}"
        for a in report["agents"]
    )
    lines.extend(report["limits"])
    return "\n".join(lines)
