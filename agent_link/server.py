from __future__ import annotations

import argparse
import atexit
import json
import os
import threading

from mcp.server.fastmcp import FastMCP

from .acp import ACP
from .antigravity import Antigravity
from .assignment import Assignment, Policy, Task, recommend
from .common import BridgeError, load_config
from .opencode import OpenCode
from .workbuddy import WorkBuddy


class Agents:
    names = ("antigravity", "opencode", "qoder", "qodercn", "workbuddy")

    def __init__(self, config=None):
        self.config = config or {}
        self.instances = {}
        self.lock = threading.Lock()

    def get(self, name):
        if name not in self.names:
            raise BridgeError("Unknown agent. Choose: " + ", ".join(self.names))
        with self.lock:
            if name not in self.instances:
                config = self.config.get(name, {})
                cls = {"antigravity": Antigravity, "opencode": OpenCode, "workbuddy": WorkBuddy}.get(name)
                self.instances[name] = cls(config) if cls else ACP(name, config)
            return self.instances[name]

    def status(self):
        result = {}
        for name in self.names:
            try:
                result[name] = self.get(name).connect()
            except Exception as exc:
                result[name] = {"connected": False, "reason": str(exc)}
        return result

    def close(self):
        for adapter in self.instances.values():
            adapter.close()


def build_server(agents):
    mcp = FastMCP("agent-link")

    @mcp.tool()
    def agents_status() -> dict:
        """Discover installed agents and desktop servers. No coding tasks are submitted.

        ACP initialization is transport health only, not account/model readiness.
        """
        return agents.status()

    @mcp.tool()
    def plan_agent_assignment(request: Assignment) -> dict:
        """Rank declared Multica candidates; read-only advice, never a task claim.

        Supply a verified input list, eligible task state, current runtime observations
        and declared profiles. The tool cannot verify those claims or authorize a retry.
        Unknown or stale availability is excluded; quota and capacity can be unknown.
        Reviewer selection requires a different agent ID, not proof of an independent session.
        """
        return recommend(request)

    @mcp.tool()
    def recommend_configured_team(
        task: Task, policy: Policy, agents: list[dict], runtimes: list[dict], runs: dict, observed_at: float
    ) -> dict:
        """Rank configured members using caller-supplied Multica observations.

        Read agents and runtimes with `multica agent list --output json` and
        `multica runtime list --output json`. For every agent on a configured
        machine, supply `multica agent tasks ID --limit 200 --output json` in
        runs keyed by agent ID. Include each agent's id, runtime_id, owner_id,
        max_concurrent_tasks, name and model; include each runtime's id,
        daemon_id, status and provider. observed_at is the Unix timestamp when
        collection began. AGENT_COOPERATION_PROFILES selects the profiles.
        The tool cannot authenticate supplied observations and does not dispatch.
        """
        from pathlib import Path

        from .team_routing import TeamProfiles, recommend_team

        path = os.environ.get("AGENT_COOPERATION_PROFILES")
        if not path:
            raise BridgeError("Set AGENT_COOPERATION_PROFILES to the team's profiles JSON file.")
        profiles = TeamProfiles.model_validate_json(Path(path).read_text(encoding="utf-8"))
        return recommend_team(task, policy, profiles, agents, runtimes, runs, observed_at=observed_at)

    @mcp.tool()
    def recommend_configured_team_from_file(task: Task, policy: Policy, snapshot_path: str) -> dict:
        """Rank using a JSON snapshot with agents, runtimes, runs and observed_at.

        Collect the unmodified JSON output of Multica's agent list, runtime list,
        and agent tasks commands. Store runs as a map keyed by agent ID; pass the
        absolute path to the local snapshot. No task is started by this tool.
        """
        from pathlib import Path

        path = Path(snapshot_path)
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > 5_000_000:
            raise BridgeError("Snapshot must be an existing absolute JSON file under 5 MB.")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict) or not {"agents", "runtimes", "runs", "observed_at"} <= snapshot.keys():
            raise BridgeError("Snapshot needs agents, runtimes, runs and observed_at fields.")
        return recommend_configured_team(
            task, policy, snapshot["agents"], snapshot["runtimes"], snapshot["runs"], snapshot["observed_at"]
        )

    @mcp.tool()
    def agent_sessions(agent: str, limit: int = 30) -> dict:
        """List sessions. ACP lists sessions created in this MCP process only."""
        return {"sessions": agents.get(agent).sessions(max(1, min(limit, 100)))}

    @mcp.tool()
    def agent_models(agent: str) -> dict:
        """List models. Qoder ACP needs a created session before reporting its model list."""
        return {"models": agents.get(agent).models()}

    @mcp.tool()
    def agent_create(agent: str, workspace: str, title: str = "Agent Link", model: str | None = None) -> dict:
        """Create a coding session in an existing absolute workspace.

        Antigravity requires a model ID. OpenCode model format is provider/model.
        Qoder uses ACP and does not take over an existing desktop IDE chat.
        WorkBuddy exposes its existing local-assistant session instead.
        """
        return agents.get(agent).create(workspace, title, model)

    @mcp.tool()
    def agent_send(agent: str, session_id: str, prompt: str) -> dict:
        """Submit a task; it may edit workspace files. submitted is not completion.

        Never retry an uncertain submission automatically. Use agent_read/agent_wait.
        WorkBuddy's session_id is local-assistant.
        """
        return agents.get(agent).send(session_id, prompt)

    @mcp.tool()
    def agent_read(agent: str, session_id: str, limit: int = 20) -> dict:
        """Read recent output, task state and errors. Do not equate transport health with success."""
        return agents.get(agent).read(session_id, max(1, min(limit, 100)))

    @mcp.tool()
    def agent_wait(agent: str, session_id: str, seconds: int = 30) -> dict:
        """Wait briefly for results. A timeout or permission request may return before completion."""
        return agents.get(agent).wait(session_id, max(1, min(seconds, 45)))

    @mcp.tool()
    def agent_permissions(agent: str, session_id: str) -> dict:
        """Inspect Qoder ACP pending permission requests; no automatic approval."""
        adapter = agents.get(agent)
        if not isinstance(adapter, ACP):
            raise BridgeError("This tool currently supports Qoder ACP only. Handle other approvals in their app.")
        return {"permissions": adapter.permissions(session_id)}

    @mcp.tool()
    def agent_permission_reply(agent: str, permission_id: str, option_id: str | None = None) -> dict:
        """Answer a pending Qoder ACP permission. Use an option ID returned by agent_permissions.

        Null option cancels. Select an approval only when the underlying action is authorized by the user.
        """
        adapter = agents.get(agent)
        if not isinstance(adapter, ACP):
            raise BridgeError("Permission replies currently support Qoder ACP only.")
        return adapter.reply(permission_id, option_id)

    return mcp


def main():
    parser = argparse.ArgumentParser(description="MCP bridge for desktop coding sessions and ACP agents")
    parser.add_argument("--config", help="Optional JSON configuration; defaults to AGENT_LINK_CONFIG")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Print connection diagnostics, then exit")
    mode.add_argument(
        "--preflight", action="store_true", help="Check local Multica prerequisites without starting agents"
    )
    mode.add_argument(
        "--multica-config", action="store_true", help="Print local Multica MCP configuration without connecting agents"
    )
    args = parser.parse_args()
    if args.preflight:
        from .preflight import preflight

        print(json.dumps(preflight(args.config), ensure_ascii=False, indent=2))
        return
    if args.multica_config:
        import os

        from .multica import mcp_config

        path = args.config or os.getenv("AGENT_LINK_CONFIG")
        try:
            load_config(path)
            result = mcp_config(path)
        except (OSError, ValueError, BridgeError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    agents = Agents(load_config(args.config))
    atexit.register(agents.close)
    if args.check:
        try:
            print(json.dumps(agents.status(), ensure_ascii=False, indent=2))
        finally:
            agents.close()
        return
    build_server(agents).run(transport="stdio")
