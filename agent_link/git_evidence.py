"""Read-only checks on a claimed Git delivery; never run project commands."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


class EvidenceError(ValueError):
    pass


def inspect_git_delivery(workspace: str, expected_commit: str, base_commit: str) -> dict:
    """Verify exact commits, ancestry, worktree cleanliness and changed paths."""
    if not all(re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", v or "") for v in (expected_commit, base_commit)):
        raise EvidenceError("Full commit hashes are required")
    root = Path(workspace)
    if not root.is_absolute() or not root.is_dir():
        raise EvidenceError("Workspace must be an existing absolute directory")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("GIT_", "MULTICA_", "COOPERATION_"))
        and key.upper() not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"}
    }
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")

    def git(*args, allow_nonzero=False):
        try:
            result = subprocess.run(
                ["git", "-c", "core.fsmonitor=false", *args],
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise EvidenceError("Git inspection did not finish") from None
        if result.returncode and not (allow_nonzero and result.returncode == 1):
            raise EvidenceError("Git inspection failed")
        return result

    top = git("rev-parse", "--show-toplevel").stdout.decode("utf-8", errors="replace").strip()
    if Path(top).resolve() != root.resolve():
        raise EvidenceError("Workspace must be the Git worktree root")
    head = git("rev-parse", "--verify", "HEAD^{commit}").stdout.decode("ascii").strip()
    expected = git("rev-parse", "--verify", expected_commit + "^{commit}").stdout.decode("ascii").strip()
    base = git("rev-parse", "--verify", base_commit + "^{commit}").stdout.decode("ascii").strip()
    if head.lower() != expected.lower():
        raise EvidenceError("Workspace HEAD does not match the claimed commit")
    ancestor = git("merge-base", "--is-ancestor", base, head, allow_nonzero=True).returncode == 0
    identity = (
        git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", head)
        .stdout.decode("utf-8", errors="replace")
        .rstrip("\n")
        .split("\0")
    )
    if len(identity) != 4:
        raise EvidenceError("Git commit identity could not be read")
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    data = git("diff", "--no-ext-diff", "--name-only", "-z", base, head, "--").stdout
    final_head = git("rev-parse", "--verify", "HEAD^{commit}").stdout.decode("ascii").strip()
    final_status = git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    if final_head != head or final_status != status:
        raise EvidenceError("Workspace changed while Git evidence was being collected")
    paths = [p.decode("utf-8", errors="replace") for p in data.split(b"\0") if p]
    return {
        "commit": head,
        "base": base,
        "base_is_ancestor": ancestor,
        "author": {"name": identity[0], "email": identity[1]},
        "committer": {"name": identity[2], "email": identity[3]},
        "clean": not bool(status),
        "changed_paths": paths[:500],
        "paths_truncated": len(paths) > 500,
        "note": "Commit author and committer are Git metadata, not proof of a GitHub account. "
        "Point-in-time local Git state only; ignored files and remote publication are not checked. "
        "No tests or independent acceptance were performed.",
    }
