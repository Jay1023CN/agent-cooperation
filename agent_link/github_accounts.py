"""Persist member-to-GitHub bindings; credentials remain in the local gh profile."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

from .github_identity import GitHubIdentityError, read_identity


class GitHubAccounts:
    def __init__(self, database: str):
        path = Path(database).expanduser()
        if not path.is_absolute() or not path.parent.is_dir():
            raise GitHubIdentityError("Account database must be in an existing absolute directory")
        self.path = path
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS member_github_accounts ("
                "member_id TEXT PRIMARY KEY, github_id INTEGER NOT NULL, github_login TEXT NOT NULL, "
                "git_name TEXT NOT NULL, git_email TEXT, gh_config_dir TEXT NOT NULL, updated_at REAL NOT NULL)"
            )

    def bind(self, member_id: str, config_dir: str, *, gh: str = "gh") -> dict:
        if not isinstance(member_id, str) or not member_id.strip():
            raise GitHubIdentityError("A workspace member ID is required")
        identity = read_identity(config_dir, gh=gh)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT github_id FROM member_github_accounts WHERE member_id = ?", (member_id,)
            ).fetchone()
            if current and current[0] != identity["github_id"]:
                raise GitHubIdentityError("Another GitHub account is already bound to this member")
            conn.execute(
                "INSERT INTO member_github_accounts VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(member_id) DO UPDATE SET github_login=excluded.github_login, "
                "git_name=excluded.git_name, git_email=excluded.git_email, "
                "gh_config_dir=excluded.gh_config_dir, updated_at=excluded.updated_at",
                (
                    member_id,
                    identity["github_id"],
                    identity["github_login"],
                    identity["git_name"],
                    identity["git_email"],
                    str(Path(config_dir).resolve()),
                    time.time(),
                ),
            )
        return {"member_id": member_id, **identity}

    def members(self) -> list[str]:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            return [row[0] for row in conn.execute("SELECT member_id FROM member_github_accounts ORDER BY member_id")]

    def resolve(self, member_id: str, *, gh: str = "gh") -> dict:
        """Recheck which account is active in the bound profile before using it."""
        with closing(sqlite3.connect(self.path)) as conn, conn:
            row = conn.execute(
                "SELECT github_id, gh_config_dir FROM member_github_accounts WHERE member_id = ?", (member_id,)
            ).fetchone()
        if row is None:
            raise GitHubIdentityError("Member has not connected a GitHub account")
        identity = read_identity(row[1], gh=gh)
        if identity["github_id"] != row[0]:
            raise GitHubIdentityError("The active GitHub account differs from the member's binding")
        return {"member_id": member_id, **identity, "gh_config_dir": row[1]}

    def apply_to_profiles(self, profiles, member_id: str, *, gh: str = "gh"):
        """Populate that member's Git identity from their currently authenticated account."""
        from .github_identity import bind_member_identity

        return bind_member_identity(profiles, member_id, self.resolve(member_id, gh=gh))

    def git_environment(self, member_id: str, *, gh: str = "gh") -> dict[str, str]:
        """Recheck the bound account and build the Git environment for one task."""
        from .github_identity import git_environment

        identity = self.resolve(member_id, gh=gh)
        return git_environment(identity["gh_config_dir"], identity)


def main():
    parser = argparse.ArgumentParser(description="Connect or inspect a member's GitHub login")
    parser.add_argument("action", choices=("login", "status"))
    parser.add_argument("--member-id", required=True, help="Multica workspace member UUID")
    parser.add_argument("--data-dir", default="~/.agent-cooperation/github", help="Local account data directory")
    parser.add_argument("--gh", default="gh", help="GitHub CLI executable")
    args = parser.parse_args()
    try:
        member_id = str(UUID(args.member_id))
        root = Path(args.data_dir).expanduser().resolve()
        if args.action == "login":
            from .github_identity import login_command

            profile = root / "members" / member_id / str(uuid4())
            profile.mkdir(parents=True, exist_ok=True)
            command, env = login_command(str(profile), args.gh)
            result = subprocess.run(command, env=env, check=False)
            if result.returncode:
                parser.exit(result.returncode, "GitHub login did not complete.\n")
            identity = GitHubAccounts(str(root / "accounts.sqlite")).bind(member_id, str(profile), gh=args.gh)
        else:
            if not (root / "accounts.sqlite").is_file():
                raise GitHubIdentityError("No GitHub accounts have been connected on this computer")
            identity = GitHubAccounts(str(root / "accounts.sqlite")).resolve(member_id, gh=args.gh)
            identity.pop("gh_config_dir", None)
        print(json.dumps(identity, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"GitHub connection unavailable: {exc}\n")


if __name__ == "__main__":
    main()
