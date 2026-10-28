"""Configure a Multica squad for project conversations and task handoffs."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

PROMPTS = Path(__file__).with_name("prompts")


def call_multica(command: str, profile: str | None, *args: str):
    argv = [command, *args, "--output", "json"]
    if profile:
        argv.extend(["--profile", profile])
    result = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Multica command failed")
    return json.loads(result.stdout)


def configure_team(command: str, profile: str | None, name: str, leader_runtime: str, worker_runtime: str):
    """Create a new squad using explicit runtimes, without overwriting existing agents."""
    agents = call_multica(command, profile, "agent", "list")
    squads = call_multica(command, profile, "squad", "list")
    leader_name, worker_name = f"{name} · 负责人", f"{name} · 执行"
    if any(item["name"] in (leader_name, worker_name) and not item.get("archived_at") for item in agents):
        raise ValueError("An active agent with this team name already exists; choose a different name.")
    if any(item["name"] == name and not item.get("archived_at") for item in squads):
        raise ValueError("An active squad with this name already exists; choose a different name.")
    runtimes = call_multica(command, profile, "runtime", "list")
    available = {item["id"]: item for item in runtimes}
    for runtime_id in (leader_runtime, worker_runtime):
        if runtime_id not in available or available[runtime_id].get("status") != "online":
            raise ValueError(f"Runtime is missing or offline: {runtime_id}")

    leader = call_multica(
        command,
        profile,
        "agent",
        "create",
        "--name",
        leader_name,
        "--runtime-id",
        leader_runtime,
        "--max-concurrent-tasks",
        "1",
        "--instructions",
        (PROMPTS / "cooperation_leader.md").read_text(encoding="utf-8"),
    )
    print(json.dumps({"created": "leader", "id": leader["id"]}, ensure_ascii=False), flush=True)
    worker = call_multica(
        command,
        profile,
        "agent",
        "create",
        "--name",
        worker_name,
        "--runtime-id",
        worker_runtime,
        "--max-concurrent-tasks",
        "1",
        "--instructions",
        (PROMPTS / "cooperation_worker.md").read_text(encoding="utf-8"),
    )
    print(json.dumps({"created": "worker", "id": worker["id"]}, ensure_ascii=False), flush=True)
    squad = call_multica(command, profile, "squad", "create", "--name", name, "--leader", leader["id"])
    print(json.dumps({"created": "squad", "id": squad["id"]}, ensure_ascii=False), flush=True)
    call_multica(
        command, profile, "squad", "member", "add", squad["id"], "--member-id", worker["id"], "--role", "执行成员"
    )
    return {"leader_id": leader["id"], "worker_id": worker["id"], "squad_id": squad["id"]}


def main():
    parser = argparse.ArgumentParser(description="Create a Multica leader and worker squad")
    parser.add_argument("--multica", default="multica", help="Path to the Multica CLI")
    parser.add_argument("--profile", help="Existing Multica CLI profile")
    parser.add_argument("--name", required=True, help="New squad name")
    parser.add_argument("--leader-runtime", required=True, help="Online Multica runtime ID")
    parser.add_argument("--worker-runtime", required=True, help="Online Multica runtime ID")
    args = parser.parse_args()
    try:
        result = configure_team(args.multica, args.profile, args.name, args.leader_runtime, args.worker_runtime)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Team setup stopped: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
