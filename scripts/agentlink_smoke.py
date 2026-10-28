"""Opt-in live MCP smoke test. Uses the caller's installed/authenticated agents."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(args):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agent_link"], env={**os.environ, "PYTHONUTF8": "1"}
    )
    results = {}
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 9

            async def call(tool, data):
                result = await session.call_tool(tool, data)
                if result.isError:
                    raise RuntimeError(str(result.content))
                return json.loads(result.content[0].text)

            for agent in args.agents:
                payload = {
                    "agent": agent,
                    "workspace": str(Path(args.workspace).resolve()),
                    "title": "Agent Link MCP smoke",
                }
                if agent == "opencode" and args.opencode_model:
                    payload["model"] = args.opencode_model
                created = await call("agent_create", payload)
                sid = created["id"]
                marker = agent.upper() + "_MCP_SMOKE_OK"
                await call(
                    "agent_send",
                    {
                        "agent": agent,
                        "session_id": sid,
                        "prompt": f"Reply exactly {marker}. Do not use tools, read files, or change files.",
                    },
                )
                result = await call("agent_wait", {"agent": agent, "session_id": sid, "seconds": 30})
                text = result.get("text") or "\n".join(
                    m.get("text", "") for m in result.get("messages", []) if m.get("type") == "assistant"
                )
                success = text.strip() == marker
                results[agent] = {
                    "success": success,
                    "session_id": sid,
                    "status": result.get("status") or result.get("outcome"),
                    "text": text,
                }
                print(json.dumps({agent: results[agent]}, ensure_ascii=False), flush=True)
    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    if not all(item["success"] for item in results.values()):
        raise SystemExit("One or more live smoke tests did not return the expected reply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Existing disposable absolute directory")
    parser.add_argument(
        "--agents", nargs="+", choices=["opencode", "qoder", "qodercn", "dsh"], default=["opencode", "qoder", "qodercn"]
    )
    parser.add_argument(
        "--opencode-model", help="Optional provider/model identifier; otherwise uses configured default"
    )
    parser.add_argument("--output", help="Optional local JSON report (do not commit personal session IDs)")
    asyncio.run(run(parser.parse_args()))
