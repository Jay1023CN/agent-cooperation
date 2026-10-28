"""Commit and push task results under a workspace member's own GitHub login.

The member authorizes GitHub CLI in a browser on each execution computer. Tokens stay in that
isolated gh profile; Arena only stores which member is bound to which GitHub account and asks
gh for credentials at push time.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4

from agent_link.github_accounts import GitHubAccounts
from agent_link.github_identity import GitHubIdentityError, git_environment, login_command

DEFAULT_DIR = "~/.pal-arena/github"
MEMBER_ID = re.compile(r"^[a-f0-9]{8,64}$")


def data_dir(config: dict | None = None) -> Path:
    return Path((config or {}).get("github_dir") or DEFAULT_DIR).expanduser().resolve()


def accounts(config: dict | None = None) -> GitHubAccounts | None:
    database = data_dir(config) / "accounts.sqlite"
    return GitHubAccounts(str(database)) if database.is_file() else None


def verified_members(config: dict, gh: str = "gh") -> list[dict]:
    """Members whose bound login is still the active account in their profile on this computer."""
    store = accounts(config)
    if store is None:
        return []
    members = []
    for member_id in store.members():
        try:
            identity = store.resolve(member_id, gh=gh)
        except GitHubIdentityError:
            continue
        if identity.get("git_email"):
            members.append(
                {"member_id": member_id, "github_id": identity["github_id"], "github_login": identity["github_login"]}
            )
    return members


def commit_environment(config: dict, expected: dict, gh: str = "gh") -> tuple[dict[str, str], dict]:
    """Recheck the member's login and build the Git environment for committing and pushing one task."""
    store = accounts(config)
    if store is None:
        raise GitHubIdentityError("This computer has no connected GitHub accounts")
    identity = store.resolve(expected["member_id"], gh=gh)
    if identity["github_id"] != expected["github_id"]:
        raise GitHubIdentityError("The active GitHub account differs from the one registered for this member")
    env = git_environment(identity["gh_config_dir"], identity)
    public = {
        "member_id": expected["member_id"],
        "github_id": identity["github_id"],
        "github_login": identity["github_login"],
        "git_name": identity["git_name"],
        "git_email": identity["git_email"],
    }
    return env, public


def author_environment(identity: dict) -> dict[str, str]:
    """Author settings for the agent process itself: its own commits carry the member's name, no credentials."""
    return {
        "GIT_AUTHOR_NAME": identity["git_name"],
        "GIT_AUTHOR_EMAIL": identity["git_email"],
        "GIT_COMMITTER_NAME": identity["git_name"],
        "GIT_COMMITTER_EMAIL": identity["git_email"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="pal-arena github", description="Connect a workspace member's GitHub account on this computer"
    )
    parser.add_argument("action", choices=("login", "status"))
    parser.add_argument("--member", help="Workspace member ID, shown in project settings")
    parser.add_argument("--config", help="worker.json; uses its github_dir when set")
    parser.add_argument("--gh", default="gh", help="GitHub CLI executable")
    args = parser.parse_args(argv)
    config = {}
    if args.config:
        config = json.loads(Path(args.config).expanduser().read_text(encoding="utf-8-sig"))
    root = data_dir(config)
    try:
        if args.action == "login":
            if not args.member or not MEMBER_ID.fullmatch(args.member):
                parser.error("--member must be the member ID shown in project settings")
            profile = root / "members" / args.member / uuid4().hex
            profile.mkdir(parents=True, exist_ok=True)
            command, env = login_command(str(profile), args.gh)
            if subprocess.run(command, env=env, check=False).returncode:
                parser.exit(1, "GitHub login did not complete.\n")
            identity = GitHubAccounts(str(root / "accounts.sqlite")).bind(args.member, str(profile), gh=args.gh)
            if not identity.get("git_email"):
                parser.exit(1, "This GitHub account predates ID-based noreply addresses and cannot sign commits yet.\n")
            print(f'已绑定：成员 {args.member} → GitHub {identity["github_login"]} <{identity["git_email"]}>')
            print("重启这台电脑的执行端后，中心会把需要该身份的任务派到这里。")
        else:
            members = verified_members(config, gh=args.gh)
            if not members:
                print("这台电脑还没有可用的 GitHub 绑定。")
            for item in members:
                print(f'{item["member_id"]} → {item["github_login"]}')
    except (GitHubIdentityError, OSError, ValueError) as exc:
        parser.exit(1, f"GitHub connection unavailable: {exc}\n")
