"""Deterministic release of independently reviewed commits on a configured worker."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import psutil

MAX_OUTPUT = 100_000


class ReleaseError(RuntimeError):
    def __init__(self, message: str, evidence: dict | None = None):
        super().__init__(message)
        self.evidence = evidence or {}


def checked_repo(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", parsed.path)
        or any(part in (".", "..") for part in parsed.path.split("/")[1:])
    ):
        raise ReleaseError("发布仓库必须是没有凭据的 HTTPS GitHub 地址")
    return value


def checked_ref(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}", value)
        or ".." in value
        or "//" in value
        or value.endswith(("/", ".", ".lock"))
        or any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))
    ):
        raise ReleaseError("发布目标分支名称无效")
    return value


def component(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ReleaseError("发布项目或任务标识无效")
    return value


def argv(value, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in value)
    ):
        raise ReleaseError(f"{label} 必须是非空参数数组，不能是 shell 字符串")
    return value


def terminate(process: subprocess.Popen):
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    _, alive = psutil.wait_procs(children, timeout=1)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass


def run(command: list[str], cwd: Path, cancelled: threading.Event, timeout: float) -> str:
    """Run an argv once, draining bounded output and terminating children on interruption."""
    if cancelled.is_set():
        raise ReleaseError("发布任务已取消")
    env = {name: value for name, value in os.environ.items() if not name.startswith("ARENA_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if command and command[0] == "git":
        command = ["git", "-c", "core.longpaths=true", *command[1:]]
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = bytearray()

    def drain():
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                return
            output.extend(chunk)
            if len(output) > MAX_OUTPUT:
                del output[:-MAX_OUTPUT]

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancelled.wait(0.05):
                raise ReleaseError("发布任务已取消")
            if time.monotonic() >= deadline:
                raise ReleaseError("发布命令超时")
        reader.join(timeout=2)
        if process.returncode:
            # Tool output may contain credential-helper or deployment secret values.
            raise ReleaseError(f"发布命令返回非零退出码 {process.returncode}，请在执行电脑检查")
        return bytes(output).decode("utf-8", errors="replace").strip()
    finally:
        if process.poll() is None:
            terminate(process)
        reader.join(timeout=2)
        process.stdout.close()


def reviewed_commits(job: dict) -> list[str]:
    project = job["project"]
    commits = []
    dependencies = job.get("dependencies") or []
    if not isinstance(dependencies, list):
        raise ReleaseError("发布任务缺少验收过的依赖任务")
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise ReleaseError("发布依赖必须是任务对象")
        review = dependency.get("review") or {}
        if (
            dependency.get("status") != "done"
            or dependency.get("revision") != project["revision"]
            or not isinstance(review, dict)
            or review.get("decision") != "accept"
            or not review.get("evidence")
            or dependency.get("channel", "shared") != "shared"
        ):
            raise ReleaseError("发布依赖必须已按当前需求版本独立验收")
        evidence = dependency.get("evidence") or {}
        commit = evidence.get("commit")
        if not commit:
            if dependency.get("capability") in ("research", "docs") and evidence.get("artifacts"):
                continue
            raise ReleaseError("发布依赖缺少可验证的 Git 提交")
        if not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit):
            raise ReleaseError("发布依赖包含无效 Git 提交标识")
        commit = commit.lower()
        if commit not in commits:
            commits.append(commit)
    if not commits:
        raise ReleaseError("至少需要一份已验收的 Git 提交才能发布")
    return commits


def empty_baseline(workspace: Path, git: Callable[..., str]) -> str:
    """Use the same empty-project ancestor as workers, without requiring a local Git identity."""
    tree = git("mktree")
    content = (
        f"tree {tree}\n"
        "author PAL Arena <arena@localhost> 946684800 +0000\n"
        "committer PAL Arena <arena@localhost> 946684800 +0000\n\n"
        "PAL Arena empty project baseline\n"
    )
    source = workspace / ".git" / "arena-empty-baseline"
    source.write_text(content, encoding="utf-8", newline="\n")
    try:
        return git("hash-object", "-t", "commit", "-w", str(source))
    finally:
        source.unlink(missing_ok=True)


def execute_release(
    job: dict,
    config: dict,
    root: Path,
    emit: Callable[[str], None],
    cancelled: threading.Event,
    checkpoint: Callable[[dict], None] | None = None,
) -> tuple[str, dict]:
    project, task = job["project"], job["task"]
    policy = project.get("policy") or {}
    auto_merge, auto_deploy = policy.get("auto_merge", False), policy.get("auto_deploy", False)
    if not isinstance(auto_merge, bool) or not isinstance(auto_deploy, bool) or not (auto_merge or auto_deploy):
        raise ReleaseError("项目未启用自动合并或部署")
    if task.get("channel", "shared") != "shared":
        raise ReleaseError("个人对话不能发布共享项目")
    profile_name = policy.get("release_profile")
    profiles = config.get("release_profiles") or {}
    if not isinstance(profile_name, str) or not profile_name or profile_name not in profiles:
        raise ReleaseError("请明确选择本机已配置的发布执行器")
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise ReleaseError("发布执行器配置必须是对象")
    validations = profile.get("validation_commands")
    if not isinstance(validations, list) or not validations:
        raise ReleaseError("发布执行器必须至少配置一个验证命令")
    validations = [argv(command, "验证命令") for command in validations]
    deployment = argv(profile.get("deploy_command"), "部署命令") if auto_deploy else None
    timeout = profile.get("timeout_seconds", 600)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 7200:
        raise ReleaseError("发布命令超时应在 0 到 7200 秒之间")
    repo = checked_repo(project.get("repo", ""))
    base_ref = checked_ref(project.get("base_ref", "main"))
    commits = reviewed_commits(job)
    project_id, task_id = component(project["id"]), component(task["id"])
    attempt = task.get("attempt", 1)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ReleaseError("发布尝试编号无效")
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "releases" / project_id / task_id / str(attempt)
    if not workspace.resolve().is_relative_to(root):
        raise ReleaseError("发布目录越出执行工作区")
    if workspace.exists():
        raise ReleaseError("当前发布尝试已有工作目录；先检查是否已发布，再显式创建新的尝试")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    branch = f"arena/release/{project_id}/{task_id}-{attempt}"
    evidence = {
        "workspace": str(workspace),
        "branch": branch,
        "revision": project["revision"],
        "reviewed_commits": commits,
        "release_profile": profile_name,
        "validation": [],
        "merge_status": "not_requested" if not auto_merge else "not_started",
        "deployment_status": "not_requested" if not auto_deploy else "not_started",
        "delivery": "local",
        "execution_uncertain": False,
    }

    def git(*args):
        return run(["git", *args], workspace, cancelled, timeout)

    def announce(text):
        emit(text)

    def record(stage: str, uncertain: bool):
        evidence.update(release_stage=stage, execution_uncertain=uncertain)
        if checkpoint:
            try:
                checkpoint(dict(evidence))
            except Exception as exc:
                raise ReleaseError("无法保存发布检查点；请检查本地回执与外部结果，本次不会自动重试", evidence) from exc

    try:
        announce("创建独立发布工作目录，读取目标分支。")
        run(["git", "clone", "--no-checkout", "--", repo, str(workspace)], root, cancelled, timeout)
        git("config", "core.longpaths", "true")
        remote_base = git("ls-remote", "--heads", "origin", f"refs/heads/{base_ref}")
        new_base = not remote_base.strip()
        if not new_base:
            git("fetch", "--no-tags", "origin", f"refs/heads/{base_ref}")
            base_commit = git("rev-parse", "FETCH_HEAD")
        else:
            base_commit = empty_baseline(workspace, git)
            for line in git("ls-remote", "origin").splitlines():
                fields = line.split()
                if len(fields) != 2 or not fields[1].startswith("refs/heads/arena/"):
                    raise ReleaseError("目标分支不存在，但仓库并非 PAL Arena 新建项目，不能自动创建目标分支")
                git("fetch", "--no-tags", "origin", fields[0])
                git("merge-base", "--is-ancestor", base_commit, fields[0])
        git("checkout", "-b", branch, base_commit)
        evidence["base_commit"] = base_commit
        evidence["creates_target_branch"] = new_base
        for index, commit in enumerate(commits, 1):
            announce(f"合并已验收成果 {index}/{len(commits)}：{commit[:12]}。")
            git("fetch", "--no-tags", "origin", commit)
            if git("rev-parse", "FETCH_HEAD").lower() != commit or git("cat-file", "-t", commit) != "commit":
                raise ReleaseError("远端获取的对象与验收提交不一致")
            git(
                "-c",
                "user.name=PAL Arena",
                "-c",
                "user.email=pal-arena@localhost",
                "merge",
                "--no-ff",
                "--no-edit",
                commit,
            )
        merged_commit = git("rev-parse", "HEAD")
        evidence["commit"] = merged_commit
        evidence["diff_stat"] = git("diff", "--stat", f"{base_commit}..{merged_commit}")[-20_000:]
        for index, command in enumerate(validations, 1):
            announce(f"运行集成验证 {index}/{len(validations)}。")
            run(command, workspace, cancelled, timeout)
            evidence["validation"].append({"step": index, "status": "passed"})
        # Build products may be untracked; the reviewed source and Git history must stay unchanged.
        git("diff", "--exit-code")
        git("diff", "--cached", "--exit-code")
        if git("rev-parse", "HEAD") != merged_commit:
            raise ReleaseError("验证步骤改变了源代码提交，必须重新验收")
        if auto_merge:
            announce("集成验证通过，检查目标分支是否发生变化。")
            remote = git("ls-remote", "--heads", "origin", f"refs/heads/{base_ref}")
            fields = remote.split()
            if (new_base and fields) or (not new_base and (not fields or fields[0] != base_commit)):
                raise ReleaseError("目标分支已变化；本次不推送，请重新整合和验证")
            evidence["merge_status"] = "outcome_unknown"
            record("merge_pending", True)
            try:
                git("push", "--porcelain", "origin", f"HEAD:refs/heads/{base_ref}")
            except Exception as exc:
                raise ReleaseError(
                    "目标分支推送结果未知；请检查 GitHub 后再决定重试，系统不会自动重发", evidence
                ) from exc
            evidence["merge_status"] = "completed"
            evidence["delivery"] = "github"
            evidence["published_ref"] = base_ref
            record("merge_confirmed", False)
            announce("已将验证通过的提交推送到项目目标分支。")
        if deployment:
            announce("执行本机预先配置的部署命令。")
            evidence["deployment_status"] = "outcome_unknown"
            record("deployment_pending", True)
            try:
                run(deployment, workspace, cancelled, timeout)
            except Exception as exc:
                raise ReleaseError("部署命令未确认成功；外部结果可能已发生，请检查后再决定重试", evidence) from exc
            evidence["deployment_status"] = "completed"
            record("deployment_confirmed", False)
            announce("部署命令执行成功。")
        result = f"已整合 {len(commits)} 份验收提交，通过 {len(validations)} 项发布验证。"
        if auto_merge:
            result += f" 已更新目标分支 {base_ref}。"
        if deployment:
            result += " 本机配置的部署命令已成功执行。"
        return result, evidence
    except ReleaseError as exc:
        if not exc.evidence:
            exc.evidence = evidence
        raise
    except Exception as exc:
        raise ReleaseError("发布执行未能完成确认；请检查执行电脑和已有发布记录后再决定重试", evidence) from exc
