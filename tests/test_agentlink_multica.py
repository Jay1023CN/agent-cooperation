import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_link.multica import mcp_config
from agent_link.server import main


def test_config_cli_does_not_start_agents_or_leak_local_secrets(tmp_path, capsys):
    config = tmp_path / "配置 with spaces.json"
    config.write_text(json.dumps({"workbuddy": {"token": "private-value"}}), encoding="utf-8")
    with (
        patch.object(sys, "argv", ["pal-agent-link", "--multica-config", "--config", str(config)]),
        patch("agent_link.server.Agents", side_effect=AssertionError("must not connect")),
    ):
        main()
    output = capsys.readouterr().out
    server = json.loads(output)["mcpServers"]["pal-agent-link"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "agent_link", "--config", str(config.resolve())]
    assert "private-value" not in output


def test_environment_config_is_pinned_for_daemon(tmp_path, monkeypatch, capsys):
    config = tmp_path / "agent.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AGENT_LINK_CONFIG", str(config))
    monkeypatch.setattr(sys, "argv", ["pal-agent-link", "--multica-config"])
    main()
    assert json.loads(capsys.readouterr().out)["mcpServers"]["pal-agent-link"]["args"][-1] == str(config)


@pytest.mark.parametrize("content", ["[]", "invalid json"])
def test_invalid_config_fails_before_emitting_configuration(tmp_path, monkeypatch, capsys, content):
    config = tmp_path / "agent.json"
    config.write_text(content, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["pal-agent-link", "--multica-config", "--config", str(config)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


def test_missing_config_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        mcp_config(str(tmp_path / "missing.json"))


def test_native_config_uses_packaged_dispatcher(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    server = mcp_config()["mcpServers"]["pal-agent-link"]
    assert server["args"] == ["internal-agent-link"]
    assert server["command"] == sys.executable


@pytest.mark.asyncio
async def test_generated_command_exposes_agent_tools_from_other_working_directory(tmp_path):
    server = mcp_config()["mcpServers"]["pal-agent-link"]
    parameters = StdioServerParameters(**server, cwd=str(tmp_path))
    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {"agents_status", "agent_create", "agent_send", "agent_read", "agent_wait"} <= names


@pytest.mark.asyncio
async def test_multica_mcp_config_delegates_and_keeps_task_identity_out_of_acp_child(tmp_path):
    fixture = Path(__file__).with_name("fake_agentlink_acp.py")
    config = tmp_path / "agent.json"
    config.write_text(json.dumps({"qoder": {"command": [sys.executable, str(fixture)]}}), encoding="utf-8")
    server = mcp_config(str(config))["mcpServers"]["pal-agent-link"]
    server["env"]["MULTICA_TOKEN"] = "host-task-secret"
    server["env"]["multiCa_workspace_id"] = "host-workspace"
    parameters = StdioServerParameters(**server, cwd=str(tmp_path))

    def payload(result):
        assert not result.isError, result.content
        return result.structuredContent or json.loads(result.content[0].text)

    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        created = await session.call_tool("agent_create", {"agent": "qoder", "workspace": str(tmp_path)})
        session_id = payload(created)["id"]
        sent = await session.call_tool(
            "agent_send", {"agent": "qoder", "session_id": session_id, "prompt": "Check fixture"}
        )
        assert not sent.isError
        waiting = await session.call_tool("agent_wait", {"agent": "qoder", "session_id": session_id, "seconds": 5})
        assert payload(waiting)["status"] == "waiting_permission"
        permission = payload(waiting)["permissions"][0]
        reply = await session.call_tool(
            "agent_permission_reply",
            {"agent": "qoder", "permission_id": permission["id"], "option_id": "once"},
        )
        assert not reply.isError
        completed = await session.call_tool("agent_wait", {"agent": "qoder", "session_id": session_id, "seconds": 5})
        assert payload(completed)["status"] == "completed"
        assert payload(completed)["text"] == "ALLOWED"
