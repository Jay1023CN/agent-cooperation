"""Small MCP transport client for an existing desktop session with stale tool schemas.

This starts only a local stdio MCP server, never a model process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(db: str, tool: str, arguments: dict) -> dict:
    entry = ["internal-handoff"] if getattr(sys, "frozen", False) else ["-B", "-m", "agent_link.desktop_handoff"]
    params = StdioServerParameters(
        command=sys.executable,
        args=[*entry, "--db", db, "--role", "desktop"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            if result.isError:
                raise RuntimeError("; ".join(item.text for item in result.content if item.type == "text"))
            return result.structuredContent or json.loads(result.content[0].text)


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "tool",
        choices=(
            "next_task",
            "task_status",
            "report_progress",
            "wait_for_update",
            "acknowledge_message",
            "complete_and_wait",
            "submit_result",
        ),
    )
    parser.add_argument("arguments", help="JSON object, or '-' to read JSON from stdin")
    args = parser.parse_args()
    arguments = json.load(sys.stdin) if args.arguments == "-" else json.loads(args.arguments)
    if not isinstance(arguments, dict):
        parser.error("arguments must be a JSON object")
    print(json.dumps(asyncio.run(call(args.db, args.tool, arguments)), ensure_ascii=False))


if __name__ == "__main__":
    main()
