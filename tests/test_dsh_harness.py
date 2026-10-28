from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_link.acp import ACP
from agent_link.common import BridgeError
from agent_link.discovery import acp_command, acp_launch_command
from arena.worker import inspect_agent, load_config, run_pal


def settings(tmp_path):
    return {
        "command": [
            sys.executable,
            str(Path(__file__).with_name("fake_dsh_acp.py")),
            str(tmp_path / "transcript.jsonl"),
        ],
        "profile": "cooperation",
    }


def transcript(tmp_path):
    return [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]


def test_dsh_profile_and_windows_npm_discovery(tmp_path, monkeypatch):
    package = tmp_path / "node_modules/@deepseek-ai/dsh"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"bin": {"dsh": "bin.mjs"}}))
    (package / "bin.mjs").write_text("")
    shim = str(tmp_path / "dsh.cmd")
    node = str(tmp_path / "node.exe")
    monkeypatch.setattr("agent_link.discovery.shutil.which", lambda name: shim if name == "dsh" else node)
    assert acp_command("dsh", {}) == [node, str((package / "bin.mjs").resolve())]
    assert acp_launch_command("dsh", {"command": ["dsh"]}) == ["dsh", "--profile", "acp"]
    for config in ({"command": "dsh"}, {"command": []}, {"command": ["dsh"], "profile": "--bad"}):
        with pytest.raises(BridgeError):
            acp_launch_command("dsh", config)


@pytest.mark.parametrize("model", [None, "configured-route"])
def test_dsh_configured_model_and_permission_roundtrip(tmp_path, model):
    agent = ACP("dsh", settings(tmp_path))
    try:
        sid = agent.create(str(tmp_path), model=model)["id"]
        assert agent.models()[0]["currentValue"] == "configured-route"
        agent.send(sid, "Complete task")
        pending = agent.wait(sid, 3)
        assert pending["status"] == "waiting_permission"
        agent.reply(pending["permissions"][0]["id"], "once")
        result = agent.wait(sid, 3)
        assert result["status"] == "completed"
        assert result["text"] == "DSH task complete"
    finally:
        agent.close()
    calls = transcript(tmp_path)
    selected = [call for call in calls if call.get("method") == "session/set_config_option"]
    assert len(selected) == int(model is not None)
    assert not any(call.get("method") == "session/set_model" for call in calls)
    assert agent.process.poll() is not None


def test_dsh_cancel_stops_task_and_owned_process(tmp_path):
    agent = ACP("dsh", settings(tmp_path))
    try:
        sid = agent.create(str(tmp_path))["id"]
        agent.send(sid, "Wait for permission")
        assert agent.wait(sid, 3)["status"] == "waiting_permission"
        agent.cancel(sid)
        assert agent.wait(sid, 3)["status"] == "stopped"
    finally:
        agent.close()
    assert agent.process.poll() is not None


def test_arena_dsh_uses_run_scoped_mcp_and_relays_permissions(tmp_path):
    class Decisions:
        pending = []

        def request(self, method, path, body=None):
            if method == "POST":
                self.pending = body["permissions"]
                return {}
            return {"decisions": [{"id": p["id"], "option_id": "once"} for p in self.pending]}

    env = {
        **os.environ,
        "ARENA_SERVER": "http://localhost:8765",
        "ARENA_RUN_TOKEN": "task-scoped",
        "ARENA_ALLOW_HTTP": "0",
    }
    output, evidence = run_pal(
        {"provider": "dsh", "adapter": settings(tmp_path)},
        tmp_path,
        "Complete task",
        env,
        Decisions(),
        threading.Event(),
        lambda _: None,
        10,
    )
    assert output == "DSH task complete"
    assert evidence == {"session_id": "dsh-task"}
    params = next(c["params"] for c in transcript(tmp_path) if c.get("method") == "session/new")
    assert params["cwd"] == str(tmp_path)
    assert params["mcpServers"][0]["name"] == "pal_arena"
    assert {"name": "ARENA_RUN_TOKEN", "value": "task-scoped"} in params["mcpServers"][0]["env"]


def test_dsh_worker_preflight_does_not_start_model(tmp_path):
    config = tmp_path / "worker.json"
    config.write_text(
        json.dumps({"agents": [{"key": "dsh", "name": "DSH", "provider": "dsh", "adapter": settings(tmp_path)}]})
    )
    agent = load_config(str(config))["agents"][0]
    with patch("agent_link.acp.spawn_owned", side_effect=AssertionError("must not launch")):
        report = inspect_agent(agent)
    assert report["installed"] is True
    assert report["supports_new"] is True
    assert report["supports_existing"] is False
    assert report["account_readiness"] == "unknown"
    assert not (tmp_path / "transcript.jsonl").exists()
