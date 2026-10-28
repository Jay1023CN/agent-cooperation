"""Local MCP handoff between a Multica host and a desktop agent.

Both endpoints must use the same machine and SQLite database. This does not
verify Multica task lineage, reserve runs, or synchronize across machines.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP

from .coordination_client import CoordinationClient, CoordinationError
from .git_evidence import inspect_git_delivery
from .handoff_store import HandoffStore
from .lease_keeper import LeaseKeeper
from .multica_runs import fetch_verified_lineage


def build_server(
    store: HandoffStore,
    role: str,
    verifier: Callable | None = None,
    reserve: Callable | None = None,
    inspect_reservation: Callable | None = None,
    check_reservation: Callable | None = None,
    keeper: LeaseKeeper | None = None,
) -> FastMCP:
    if role not in ("host", "desktop"):
        raise ValueError("Role must be host or desktop")
    mcp = FastMCP("local-desktop-handoff")

    if role == "host":

        @mcp.tool()
        def offer_task(lineage: str, task_id: str, workspace: str, prompt: str, issue_id: str = "") -> dict:
            """Offer once after independently verifying the full Multica retry lineage and workspace.

            Do not include tokens. A local database cannot prevent retries on another computer.
            """
            path = Path(workspace).expanduser()
            if not path.is_absolute() or not path.is_dir() or not prompt.strip():
                raise ValueError("An existing absolute workspace and nonempty prompt are required")
            if verifier is not None and verifier(task_id, issue_id)["lineage"] != lineage:
                raise ValueError("Lineage does not match the Multica task history")
            if reserve is not None:
                reserve(lineage, task_id, store.instance_id())
                if verifier is None or verifier(task_id, issue_id)["lineage"] != lineage:
                    raise ValueError("Multica task could not be rechecked after reservation")
            state = store.create(lineage, task_id, {"workspace": str(path.resolve()), "prompt": prompt})
            if keeper is not None and state in ("pending", "claimed"):
                keeper.start(lineage, task_id, issue_id)
            return {"state": state, "task_id": task_id}

        @mcp.tool()
        def inspect_task(lineage: str) -> dict:
            """Read a local handoff and candidate result; this does not update Multica."""
            return store.inspect(lineage) or {"state": "missing"}

        @mcp.tool()
        def verify_git_delivery(lineage: str, expected_commit: str, base_commit: str) -> dict:
            """Check submitted Git state and changed files without running project tests."""
            return inspect_git_delivery(store.submitted_workspace(lineage), expected_commit, base_commit)

        @mcp.tool()
        def renew_task(lineage: str, task_id: str, issue_id: str = "") -> dict:
            """Renew for 60 seconds only after verifying this exact Multica run is still active.

            Renew frequently; an expired lease cannot be revived automatically.
            """
            if verifier is not None and verifier(task_id, issue_id)["lineage"] != lineage:
                raise ValueError("Lineage does not match the Multica task history")
            if check_reservation is not None:
                check_reservation(lineage, task_id, store.instance_id())
            return {"lease_until": store.renew(lineage, task_id)}

        @mcp.tool()
        def cancel_task(lineage: str) -> dict:
            """Stop accepting a result; a desktop session may still be running."""
            state = store.cancel(lineage)
            if keeper is not None:
                keeper.stop(lineage)
            return {"state": state}

        if inspect_reservation is not None:

            @mcp.tool()
            def inspect_coordinator(lineage: str) -> dict:
                """Inspect a central reservation; it does not authorize a retry."""
                return inspect_reservation(lineage)

    else:

        @mcp.tool()
        def list_tasks() -> dict:
            """List pending local handoffs without claiming one."""
            return {"tasks": store.pending()}

        @mcp.tool()
        def claim_task(lineage: str) -> dict:
            """Claim a task once; lost responses require inspection, not another claim."""
            return store.claim(lineage)

        @mcp.tool()
        def task_status(lineage: str, handle: str) -> dict:
            """Check whether a claimed task was cancelled before continuing work."""
            return {"state": store.desktop_state(lineage, handle)}

        @mcp.tool()
        def submit_result(lineage: str, handle: str, summary: str, evidence: list[str]) -> dict:
            """Submit a candidate result for the host to inspect and independently review."""
            if not summary.strip() or not evidence or not all(isinstance(x, str) and x.strip() for x in evidence):
                raise ValueError("A summary and at least one evidence item are required")
            store.submit(lineage, handle, {"summary": summary, "evidence": evidence})
            return {"state": "submitted"}

    return mcp


def main():
    parser = argparse.ArgumentParser(description="Local desktop handoff MCP")
    parser.add_argument("--db", required=True, help="Absolute path to a private shared SQLite database")
    parser.add_argument("--role", required=True, choices=("host", "desktop"))
    parser.add_argument("--multica-task", action="store_true", help="Verify host offers and renewals with Multica")
    parser.add_argument("--coordinator-url", help="Single shared reservation service; requires --multica-task")
    parser.add_argument(
        "--print-config", action="store_true", help="Print MCP configuration without starting the service"
    )
    args = parser.parse_args()
    path = Path(args.db).expanduser()
    if not path.is_absolute() or not path.parent.is_dir():
        parser.error("--db must be an absolute path inside an existing private directory")
    if args.coordinator_url and not args.multica_task:
        parser.error("--coordinator-url requires --multica-task")
    if args.multica_task and args.role != "host":
        parser.error("--multica-task is only valid for the host")
    if args.print_config:
        if getattr(sys, "frozen", False):
            parser.error("configuration generation is only available from an installed Python environment")
        command_args = ["-m", "agent_link.desktop_handoff", "--db", str(path.resolve()), "--role", args.role]
        if args.multica_task:
            command_args.append("--multica-task")
        if args.coordinator_url:
            try:
                CoordinationClient(args.coordinator_url, "x" * 32)
            except CoordinationError:
                parser.error("invalid coordinator URL")
            command_args.extend(["--coordinator-url", args.coordinator_url])
        print(
            json.dumps(
                {
                    "mcpServers": {
                        "desktop-handoff": {"command": sys.executable, "args": command_args, "env": {"PYTHONUTF8": "1"}}
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    verifier = None
    if args.multica_task:
        required = (
            "MULTICA_SERVER_URL",
            "MULTICA_TOKEN",
            "MULTICA_TASK_ID",
            "MULTICA_AGENT_ID",
            "MULTICA_WORKSPACE_ID",
        )
        if not all(os.environ.get(name) for name in required):
            parser.error("--multica-task requires the current Multica task environment")
        task_id = os.environ["MULTICA_TASK_ID"]

        def verifier(requested_id, issue_id):
            if requested_id != task_id:
                raise ValueError("Task ID does not match the current Multica run")
            return fetch_verified_lineage(
                server_url=os.environ["MULTICA_SERVER_URL"],
                token=os.environ["MULTICA_TOKEN"],
                task_id=task_id,
                agent_id=os.environ["MULTICA_AGENT_ID"],
                workspace_id=os.environ["MULTICA_WORKSPACE_ID"],
                issue_id=issue_id,
            )

    reservation = None
    inspection = None
    reservation_check = None
    if args.coordinator_url:
        coordinator = CoordinationClient(args.coordinator_url, os.getenv("COOPERATION_COORDINATION_TOKEN", ""))

        def reservation(lineage, requested_id, instance_id):
            coordinator.reserve(os.environ["MULTICA_WORKSPACE_ID"], lineage, requested_id, instance_id)

        def inspection(lineage):
            return coordinator.inspect(os.environ["MULTICA_WORKSPACE_ID"], lineage)

        def reservation_check(lineage, requested_id, instance_id):
            coordinator.check(os.environ["MULTICA_WORKSPACE_ID"], lineage, requested_id, instance_id)

    store = HandoffStore(path)
    keeper = LeaseKeeper(store, verifier, reservation_check) if verifier and reservation_check else None
    if keeper is not None:
        atexit.register(keeper.close)
    build_server(store, args.role, verifier, reservation, inspection, reservation_check, keeper).run(transport="stdio")


if __name__ == "__main__":
    main()
