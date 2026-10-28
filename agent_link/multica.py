from __future__ import annotations

import sys
from pathlib import Path


def mcp_config(config_path: str | None = None) -> dict:
    """Describe this installation as a local Multica-managed MCP server."""
    args = ["internal-agent-link"] if getattr(sys, "frozen", False) else ["-m", "agent_link"]
    if config_path:
        path = Path(config_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("Agent Link configuration must be a file.")
        args.extend(["--config", str(path)])
    return {
        "mcpServers": {
            "pal-agent-link": {
                "command": sys.executable,
                "args": args,
                "env": {"PYTHONUTF8": "1"},
            }
        }
    }
