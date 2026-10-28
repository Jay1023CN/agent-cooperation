"""Check the actual MCP stdio protocol against a local Arena server."""

import asyncio
import json
import os
import socket
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from arena.api import create_app


@pytest.mark.asyncio
async def test_desktop_stdio_client_can_deliver_to_independent_review(tmp_path):
    app = create_app(tmp_path / "server")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    server_thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert server.started
    origin = f"http://127.0.0.1:{port}"
    owner = (tmp_path / "server" / "owner-token.txt").read_text()
    http = httpx.Client(base_url=origin, headers={"Authorization": "Bearer " + owner})
    try:
        token = http.post("/api/invites", json={"name": "Desktop", "kind": "machine"}).json()["token"]
        project = http.post("/api/projects", json={"name": "MCP stdio"}).json()
        task = http.post(
            f'/api/projects/{project["id"]}/tasks', json={"title": "stdio task", "prompt": "Write result.txt"}
        ).json()
        config = {
            "server": origin,
            "workspace_root": str(tmp_path / "workspaces"),
            "name": "Desktop",
            "slots": 1,
            "agents": [
                {
                    "key": "gui",
                    "name": "GUI",
                    "provider": "desktop_mcp",
                    "roles": ["worker"],
                    "capabilities": ["code"],
                    "mode": "new",
                }
            ],
        }
        config_path, token_path = tmp_path / "worker.json", tmp_path / "token.txt"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        token_path.write_text(token, encoding="utf-8")
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "arena.desktop_mcp", "--config", str(config_path), "--token-file", str(token_path)],
            cwd=str(Path(__file__).resolve().parents[1]),
            env={**os.environ, "PYTHONUTF8": "1"},
        )

        def unpack(result):
            assert not result.isError, result.content
            return result.structuredContent or json.loads(result.content[0].text)

        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"next_task", "read_file", "write_file", "start_command", "submit_result"} <= names
            job = unpack(await session.call_tool("next_task", {}, read_timeout_seconds=timedelta(seconds=40)))
            assert job["state"] == "ready" and job["task_id"] == task["id"]
            assert token not in json.dumps(job)
            unpack(
                await session.call_tool(
                    "write_file", {"handle": job["handle"], "path": "result.txt", "content": "done"}
                )
            )
            result = unpack(
                await session.call_tool(
                    "submit_result",
                    {
                        "handle": job["handle"],
                        "nonce": job["nonce"],
                        "requirement_revision": job["requirement_revision"],
                        "summary": "Wrote result.txt",
                    },
                )
            )
            assert result["state"] == "submitted"
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = http.get(f'/api/tasks/{task["id"]}').json()
                if status["status"] == "in_review":
                    break
                await asyncio.sleep(0.05)
            assert status["status"] == "in_review", status
            assert len(status["evidence"]["commit"]) == 40
    finally:
        http.close()
        server.should_exit = True
        server_thread.join(10)
        listener.close()
