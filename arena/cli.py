from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "workspaces":
        from .workspaces import main as workspaces_main

        sys.argv.pop(1)
        workspaces_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "github":
        from .github import main as github_main

        github_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "maintenance":
        from .maintenance import main as maintenance_main

        sys.argv.pop(1)
        maintenance_main()
        return
    parser = argparse.ArgumentParser(description="PAL Arena — self-hosted development workspace")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Start the workspace on an always-on computer")
    serve.add_argument("--data", default="~/.pal-arena/server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--quota-gb", type=float, default=5)
    serve.add_argument(
        "--share", action="store_true", help="Also publish this workspace to your Tailscale network over HTTPS"
    )
    share = sub.add_parser("share", help="Publish a running workspace to your Tailscale network over HTTPS")
    share.add_argument("--port", type=int, default=8765)
    share.add_argument("--data", default="~/.pal-arena/server")
    config = sub.add_parser("worker-config", help="Generate a machine configuration without launching agents")
    config.add_argument("--name", required=True)
    config.add_argument("--server", default="http://127.0.0.1:8765")
    config.add_argument("--slots", type=int, default=2)
    config.add_argument("--output", help="Write UTF-8 JSON to a new file; refuses to overwrite existing settings")
    doctor = sub.add_parser("doctor", help="Check installation without submitting a model task")
    doctor.add_argument("--config", help="Worker JSON configuration to inspect")
    doctor.add_argument("--json", action="store_true", dest="json_output")
    doctor.add_argument("--output", help="Save a JSON diagnostic report")
    doctor.add_argument("--no-network", action="store_true", help="Skip local transport and server probes")
    sub.add_parser("maintenance", help="Backup, restore, and generate startup services")
    sub.add_parser("workspaces", help="Inspect or clean verified, synchronized workspaces")
    sub.add_parser("github", help="Connect a member's GitHub account so task commits are pushed as them")
    args = parser.parse_args()
    if args.command == "doctor":
        from .doctor import diagnose, render

        report = diagnose(args.config, probe_transport=not args.no_network)
        if args.output:
            output = Path(args.output).expanduser().resolve()
            output.write_text(render(report, json_output=True) + "\n", encoding="utf-8")
            output.chmod(0o600)
        print(render(report, json_output=args.json_output))
        if report["status"] == "error":
            raise SystemExit(1)
        return
    if args.command == "worker-config":
        value = {
            "name": args.name,
            "server": args.server,
            "slots": max(1, min(args.slots, 8)),
            "workspace_root": "~/.pal-arena/workspaces",
            "agents": [
                {
                    "key": "planner",
                    "name": "项目负责人",
                    "provider": "codex",
                    "roles": ["leader"],
                    "capabilities": ["planning", "code", "research"],
                    "preference": 10,
                },
                {
                    "key": "builder",
                    "name": "Qoder 执行",
                    "provider": "qoder",
                    "roles": ["worker"],
                    "capabilities": ["code", "web", "docs", "research"],
                    "preference": 5,
                },
                {
                    "key": "reviewer",
                    "name": "Claude 独立验收",
                    "provider": "claude",
                    "roles": ["reviewer"],
                    "capabilities": ["code", "web", "docs", "research"],
                    "preference": 10,
                },
            ],
        }
        serialized = json.dumps(value, ensure_ascii=False, indent=2)
        if args.output:
            try:
                with Path(args.output).expanduser().open("x", encoding="utf-8") as output:
                    output.write(serialized + "\n")
            except OSError:
                parser.error("Cannot create configuration file; check its parent directory and use a new filename")
        else:
            print(serialized)
        return
    if args.command == "share":
        from .network import describe, share

        result = share(args.port, Path(args.data).expanduser().resolve())
        print(describe(result))
        if result["state"] != "shared":
            raise SystemExit(1)
        return
    if args.quota_gb <= 0:
        parser.error("--quota-gb must be positive")
    try:
        import uvicorn

        from .api import create_app
    except ImportError as exc:
        parser.error(f'Install platform dependencies with: python -m pip install -e ".[platform]" ({exc.name})')
    root = Path(args.data).expanduser().resolve()
    app = create_app(root, quota=int(args.quota_gb * 1_000_000_000))
    print(f"PAL Arena: http://{args.host}:{args.port}")
    print(f'Owner login token is stored locally in {root / "owner-token.txt"}')
    if args.share:
        from .network import describe, share

        print(describe(share(args.port, root)))
    else:
        print("Other computers: add --share, or run `pal-arena share` after Tailscale is signed in.")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("ARENA_TRUSTED_PROXIES", "127.0.0.1"),
    )
