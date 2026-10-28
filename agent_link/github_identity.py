"""Read a member's browser-authorized GitHub CLI identity without reading tokens."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class GitHubIdentityError(ValueError):
    pass


def isolated_environment(config_dir: str) -> dict[str, str]:
    path = Path(config_dir).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise GitHubIdentityError("GitHub CLI configuration must be an existing absolute directory")
    env = dict(os.environ)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        env.pop(name, None)
    env["GH_CONFIG_DIR"] = str(path.resolve())
    env["GH_PROMPT_DISABLED"] = "1"
    return env


def git_environment(config_dir: str, identity: dict) -> dict[str, str]:
    """Build a task-local Git environment for a previously verified member login."""
    name, email = identity.get("git_name"), identity.get("git_email")
    if not all(isinstance(value, str) and value.strip() for value in (name, email)):
        raise GitHubIdentityError("A verified Git commit name and email are required")
    env = isolated_environment(config_dir)
    for key in list(env):
        if key.upper().startswith("GIT_"):
            env.pop(key, None)
    env.update(
        GIT_AUTHOR_NAME=name,
        GIT_AUTHOR_EMAIL=email,
        GIT_COMMITTER_NAME=name,
        GIT_COMMITTER_EMAIL=email,
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="credential.helper",
        GIT_CONFIG_VALUE_0="",
        GIT_CONFIG_KEY_1="credential.https://github.com.helper",
        GIT_CONFIG_VALUE_1="!gh auth git-credential",
    )
    return env


def login_command(config_dir: str, gh: str = "gh") -> tuple[list[str], dict[str, str]]:
    """Return an interactive web-login command; the caller must run it for the member."""
    env = isolated_environment(config_dir)
    env.pop("GH_PROMPT_DISABLED", None)
    return [gh, "auth", "login", "--hostname", "github.com", "--web", "--git-protocol", "https"], env


def identity_from_user(user: dict) -> dict:
    """Derive the documented ID-based noreply address for newer GitHub accounts."""
    if not isinstance(user, dict) or not isinstance(user.get("login"), str) or type(user.get("id")) is not int:
        raise GitHubIdentityError("GitHub did not return a usable account identity")
    try:
        created = datetime.fromisoformat(user["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("Missing timezone")
    except (KeyError, TypeError, ValueError, AttributeError):
        raise GitHubIdentityError("GitHub account creation date is unavailable") from None
    modern = created >= datetime(2017, 7, 19, tzinfo=timezone.utc)
    return {
        "github_id": user["id"],
        "github_login": user["login"],
        "git_name": user.get("name") or user["login"],
        "git_email": f'{user["id"]}+{user["login"]}@users.noreply.github.com' if modern else None,
        "email_source": "github_id_noreply" if modern else "unresolved",
    }


def read_identity(config_dir: str, gh: str = "gh") -> dict:
    """Read /user through the member's isolated gh profile, without emitting credentials."""
    try:
        result = subprocess.run(
            [gh, "api", "--hostname", "github.com", "user"],
            env=isolated_environment(config_dir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubIdentityError("Unable to contact GitHub using this member's login") from None
    if result.returncode:
        raise GitHubIdentityError("GitHub login is missing, expired, or unavailable")
    try:
        return identity_from_user(json.loads(result.stdout))
    except (ValueError, TypeError):
        raise GitHubIdentityError("GitHub account response could not be read") from None


def bind_member_identity(profiles, member_id: str, identity: dict):
    """Return updated profiles for the authenticated member without changing other members."""
    from .team_routing import TeamProfiles

    profiles = TeamProfiles.model_validate(profiles.model_dump())
    member = next((item for item in profiles.members if item.id == member_id), None)
    if member is None:
        raise GitHubIdentityError("Workspace member is not configured")
    if member.github_id is not None and member.github_id != identity.get("github_id"):
        raise GitHubIdentityError("Another GitHub account is already bound to this member")
    if (
        member.github_id is None
        and member.github_login
        and member.github_login.lower() != identity["github_login"].lower()
    ):
        raise GitHubIdentityError("The GitHub login does not match this member's existing account")
    member.github_id = identity["github_id"]
    member.github_login = identity["github_login"]
    member.git_name = identity["git_name"]
    member.git_email = identity["git_email"]
    return profiles


def main():
    parser = argparse.ArgumentParser(description="Connect a member's GitHub account with browser authorization")
    parser.add_argument("action", choices=("login", "identity"))
    parser.add_argument("--config-dir", required=True, help="Existing absolute directory for this member's gh profile")
    parser.add_argument("--gh", default="gh", help="GitHub CLI executable")
    args = parser.parse_args()
    try:
        if args.action == "login":
            command, env = login_command(args.config_dir, args.gh)
            result = subprocess.run(command, env=env, check=False)
            if result.returncode:
                parser.exit(result.returncode, "GitHub login did not complete.\n")
        identity = read_identity(args.config_dir, args.gh)
        print(json.dumps(identity, ensure_ascii=False))
    except (GitHubIdentityError, OSError) as exc:
        parser.exit(1, f"GitHub identity unavailable: {exc}\n")


if __name__ == "__main__":
    main()
