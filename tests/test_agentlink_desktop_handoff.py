import json
import os
import subprocess
import sys
import threading
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_link.desktop_handoff import build_server
from agent_link.handoff_store import HandoffStore


@pytest.mark.asyncio
async def test_host_and_desktop_handoff_through_separate_mcp_processes(tmp_path):
    db = tmp_path / "handoffs.sqlite"

    async def connect(stack, role):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_link.desktop_handoff", "--db", str(db), "--role", role],
            cwd=str(tmp_path),
        )
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    def payload(result):
        assert not result.isError, result.content
        return result.structuredContent or json.loads(result.content[0].text)

    async with AsyncExitStack() as stack:
        host = await connect(stack, "host")
        desktop = await connect(stack, "desktop")
        assert {x.name for x in (await host.list_tools()).tools} == {
            "offer_task",
            "inspect_task",
            "verify_git_delivery",
            "renew_task",
            "cancel_task",
        }
        assert {x.name for x in (await desktop.list_tools()).tools} == {
            "list_tasks",
            "claim_task",
            "task_status",
            "submit_result",
        }
        first_workspace = tmp_path / "first-workspace"
        first_workspace.mkdir()
        offer = {"lineage": "root", "task_id": "run-1", "workspace": str(first_workspace), "prompt": "inspect project"}
        assert payload(await host.call_tool("offer_task", offer))["state"] == "pending"
        assert "lease_until" in payload(await host.call_tool("renew_task", {"lineage": "root", "task_id": "run-1"}))
        assert payload(await desktop.call_tool("list_tasks", {}))["tasks"][0]["lineage"] == "root"
        claim = payload(await desktop.call_tool("claim_task", {"lineage": "root"}))
        assert claim["payload"]["prompt"] == "inspect project"
        assert (await desktop.call_tool("claim_task", {"lineage": "root"})).isError
        assert (await host.call_tool("offer_task", {**offer, "task_id": "retry"})).isError
        result = {"lineage": "root", "handle": claim["handle"], "summary": "done", "evidence": ["commit abc"]}
        assert payload(await desktop.call_tool("submit_result", result))["state"] == "submitted"
        assert payload(await desktop.call_tool("submit_result", result))["state"] == "submitted"
        assert payload(await host.call_tool("inspect_task", {"lineage": "root"}))["result"]["evidence"] == [
            "commit abc"
        ]

        other_workspace = tmp_path / "other-workspace"
        other_workspace.mkdir()
        await host.call_tool(
            "offer_task", {**offer, "lineage": "second", "task_id": "run-2", "workspace": str(other_workspace)}
        )
        claim = payload(await desktop.call_tool("claim_task", {"lineage": "second"}))
        assert payload(await host.call_tool("cancel_task", {"lineage": "second"}))["state"] == "cancelled"
        assert (
            payload(await desktop.call_tool("task_status", {"lineage": "second", "handle": claim["handle"]}))["state"]
            == "cancelled"
        )
        assert (
            await desktop.call_tool("submit_result", {**result, "lineage": "second", "handle": claim["handle"]})
        ).isError

    async with AsyncExitStack() as stack:
        host = await connect(stack, "host")
        assert payload(await host.call_tool("inspect_task", {"lineage": "root"}))["state"] == "submitted"
        assert payload(await host.call_tool("inspect_task", {"lineage": "second"}))["state"] == "cancelled"


@pytest.mark.asyncio
async def test_verified_host_rechecks_before_offer_and_renew(tmp_path):
    store = HandoffStore(tmp_path / "db")
    state = {"active": True}

    def verifier(task_id, issue_id):
        if not state["active"] or task_id != "run" or issue_id != "issue":
            raise ValueError("Multica run is not active")
        return {"lineage": "root"}

    host = build_server(store, "host", verifier)
    params = {"lineage": "root", "task_id": "run", "workspace": str(tmp_path), "prompt": "work", "issue_id": "issue"}
    await host.call_tool("offer_task", params)
    assert store.inspect("root")["state"] == "pending"
    await host.call_tool("renew_task", {"lineage": "root", "task_id": "run", "issue_id": "issue"})
    state["active"] = False
    with pytest.raises(Exception, match="not active"):
        await host.call_tool("renew_task", {"lineage": "root", "task_id": "run", "issue_id": "issue"})
    with pytest.raises(Exception, match="not active"):
        await host.call_tool("offer_task", {**params, "lineage": "new", "task_id": "other"})
    assert store.inspect("new") is None


@pytest.mark.asyncio
async def test_verified_host_reads_multica_before_offering_and_renewing(tmp_path):
    issue = "12345678-1234-1234-1234-123456789abc"
    task = "task-one"
    status = {"value": "running"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != f"/api/issues/{issue}/task-runs" or self.headers.get("Authorization") != "Bearer mat_test":
                self.send_response(403)
                self.end_headers()
                return
            body = json.dumps(
                [
                    {
                        "id": task,
                        "agent_id": "agent",
                        "workspace_id": "workspace",
                        "issue_id": issue,
                        "status": status["value"],
                        "attribution": {},
                        "attempt": 1,
                    }
                ]
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "MULTICA_SERVER_URL": f"http://127.0.0.1:{server.server_port}",
            "MULTICA_TOKEN": "mat_test",
            "MULTICA_TASK_ID": task,
            "MULTICA_AGENT_ID": "agent",
            "MULTICA_WORKSPACE_ID": "workspace",
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_link.desktop_handoff", "--db", str(tmp_path / "db"), "--role", "host", "--multica-task"],
            env=env,
            cwd=str(tmp_path),
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            offered = await session.call_tool(
                "offer_task",
                {"lineage": task, "task_id": task, "issue_id": issue, "workspace": str(tmp_path), "prompt": "work"},
            )
            assert not offered.isError
            status["value"] = "cancelled"
            renewed = await session.call_tool("renew_task", {"lineage": task, "task_id": task, "issue_id": issue})
            assert renewed.isError
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_config_generation_is_stable_and_never_includes_tokens(tmp_path):
    env = {**os.environ, "MULTICA_TOKEN": "mat_secret", "COOPERATION_COORDINATION_TOKEN": "private_secret"}
    db = tmp_path / "handoff.sqlite"
    cmd = [
        sys.executable,
        "-m",
        "agent_link.desktop_handoff",
        "--db",
        str(db),
        "--role",
        "host",
        "--multica-task",
        "--coordinator-url",
        "https://coordinator.example",
        "--print-config",
    ]
    result = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert "mat_secret" not in result.stdout and "private_secret" not in result.stdout
    config = json.loads(result.stdout)["mcpServers"]["desktop-handoff"]
    assert config["command"] == sys.executable
    assert config["args"][-2:] == ["--coordinator-url", "https://coordinator.example"]
    assert not db.exists()
    bad = subprocess.run(
        [*cmd[:-2], "http://remote.example", "--print-config"], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert bad.returncode != 0 and "invalid coordinator URL" in bad.stderr
