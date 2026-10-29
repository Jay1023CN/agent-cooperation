"""Conservative cleanup of verified, synchronized managed workspaces."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

MAX_RECEIPT_BYTES = 512_000
COMPONENT = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
COMMIT = re.compile(r"[a-fA-F0-9]{40}(?:[a-fA-F0-9]{24})?\Z")
GITHUB = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class CleanupError(ValueError):
    pass


class WorkspaceBusy(CleanupError):
    pass


def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _safe_path(path: Path):
    """Reject symlinks and Windows reparse points in every existing ancestor."""
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            if _linked(part):
                raise CleanupError("工作区路径包含符号链接或目录联接，已保留")


def _root(path) -> Path:
    root = Path(path).expanduser().absolute()
    _safe_path(root)
    if not root.is_dir():
        raise CleanupError("工作区根目录不存在")
    return root


@contextlib.contextmanager
def workspace_lock(root, *, blocking=False):
    """Hold the same process lock for a worker lifetime or an explicit cleanup."""
    root = _root(root)
    path = root / ".arena-workspaces.lock"
    _safe_path(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b", buffering=0) as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise CleanupError("工作区锁必须是普通文件")
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            raise WorkspaceBusy("此工作区的执行端或清理程序正在运行；停止执行端后再清理") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _git(path, *args):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ARENA_")
        and key not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"}
    }
    environment.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_OPTIONAL_LOCKS="0")
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.longpaths=true",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.https.allow=always",
                *args,
            ],
            cwd=path,
            env=environment,
            capture_output=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CleanupError("Git 检查未完成，已保留工作区") from None
    if result.returncode:
        # Git stderr may contain credentials from local credential helpers.
        raise CleanupError("Git 检查未通过，已保留工作区")
    return result.stdout.decode("utf-8", errors="replace").strip()


def _branch(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}", value)
        and ".." not in value
        and "//" not in value
        and not value.endswith(("/", ".", ".lock"))
        and not any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))
    )


def verify_remote(remote: str, branch: str, commit: str) -> bool:
    """Check remote reachability using disposable metadata, never changing the candidate."""
    if not GITHUB.fullmatch(remote) or not _branch(branch) or not COMMIT.fullmatch(commit):
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="pal-arena-remote-check-") as temporary:
            path = Path(temporary)
            _git(path, "init", "--bare")
            _git(path, "remote", "add", "origin", remote)
            # URL rewrites must not silently change the service or add embedded credentials.
            if _git(path, "remote", "get-url", "--all", "origin") != remote:
                return False
            reference = "refs/heads/" + branch
            lines = _git(path, "ls-remote", "--heads", "origin", reference).splitlines()
            if len(lines) != 1:
                return False
            fields = lines[0].split()
            if len(fields) != 2 or fields[1] != reference or not COMMIT.fullmatch(fields[0]):
                return False
            tip = fields[0].lower()
            if tip == commit.lower():
                return True
            _git(path, "fetch", "--no-tags", "--filter=blob:none", "origin", reference)
            if _git(path, "rev-parse", "FETCH_HEAD").lower() != tip:
                return False
            if _git(path, "cat-file", "-t", commit) != "commit":
                return False
            _git(path, "merge-base", "--is-ancestor", commit, tip)
            return True
    except CleanupError:
        return False


def _tree_size(path):
    total = 0
    for folder, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            entry = Path(folder) / name
            if _linked(entry):
                raise CleanupError("工作区内包含符号链接或目录联接，已保留")
            if entry.is_file():
                total += entry.stat().st_size
            elif not entry.is_dir():
                raise CleanupError("工作区内包含特殊文件，已保留")
    return total


def _receipt(root, item):
    path = root / "_receipts" / item["task_id"] / f'{item["attempt"]}.json'
    _safe_path(path)
    if not path.is_file() or path.stat().st_size > MAX_RECEIPT_BYTES:
        raise CleanupError("缺少有效执行回执，已保留")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise CleanupError("执行回执无法读取，已保留") from None
    if not isinstance(receipt, dict) or any(
        receipt.get(key) != item[key] for key in ("project_id", "task_id", "attempt")
    ):
        raise CleanupError("执行回执与目录不匹配，已保留")
    return path, receipt


def inventory(root) -> list[dict]:
    """Enumerate only the two managed layouts; other directories are never candidates."""
    root = _root(root)
    items = []
    for base, release in ((root, False), (root / "releases", True)):
        if not base.exists():
            continue
        _safe_path(base)
        for project in base.iterdir():
            if not COMPONENT.fullmatch(project.name) or project.name.startswith("_") or project.name == "releases":
                continue
            if _linked(project) or not project.is_dir():
                continue
            for task in project.iterdir():
                if not COMPONENT.fullmatch(task.name) or task.name.startswith("_"):
                    continue
                if _linked(task) or not task.is_dir():
                    continue
                for path in task.iterdir():
                    if not re.fullmatch(r"[1-9][0-9]{0,8}", path.name):
                        continue
                    if not path.is_dir() and not path.is_symlink():
                        continue
                    item = {
                        "workspace": str(path),
                        "project_id": project.name,
                        "task_id": task.name,
                        "attempt": int(path.name),
                        "release": release,
                        "updated": path.lstat().st_mtime,
                    }
                    try:
                        _, receipt = _receipt(root, item)
                        updated = receipt.get("updated")
                        if isinstance(updated, (int, float)) and not isinstance(updated, bool):
                            item["updated"] = updated
                    except (CleanupError, OSError):
                        pass
                    items.append(item)
    return items


def _input_cache(path, evidence):
    folder = path / ".arena-inputs"
    if not folder.exists():
        return
    proofs = evidence.get("input_artifacts", [])
    if not isinstance(proofs, list):
        raise CleanupError("下载缓存缺少校验记录，已保留")
    expected = {
        proof["id"]: proof.get("sha256")
        for proof in proofs
        if isinstance(proof, dict) and isinstance(proof.get("id"), str)
    }
    seen = set()
    for entry in folder.rglob("*"):
        if not entry.is_file():
            continue
        parts = entry.relative_to(folder).parts
        if len(parts) != 2 or parts[0] not in expected or parts[0] in seen:
            raise CleanupError("下载缓存包含未确认文件，已保留")
        checksum = hashlib.sha256()
        with entry.open("rb") as content:
            for block in iter(lambda: content.read(1024 * 1024), b""):
                checksum.update(block)
        if checksum.hexdigest() != expected[parts[0]]:
            raise CleanupError("下载缓存已修改或缺少校验记录，已保留")
        seen.add(parts[0])


def _local_contents(path, commit, evidence):
    """Inspect again after remote I/O so changes made during verification are retained."""
    _safe_path(path)
    size = _tree_size(path)
    if (path / "arena-exports").exists():
        raise CleanupError("工作区含导出产物，始终保留")
    if not (path / ".git").is_dir() or Path(_git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve():
        raise CleanupError("不是独立的受管理 Git 工作区，已保留")
    if _git(path, "rev-parse", "HEAD").lower() != commit.lower():
        raise CleanupError("本地提交已变化，已保留")
    index = _git(path, "ls-files", "-v", "-z")
    if any(entry and (entry[0].islower() or entry[0] == "S") for entry in index.split("\0")):
        raise CleanupError("Git 索引配置了跳过工作区检查，已保留")
    if _git(path, "stash", "list"):
        raise CleanupError("工作区存在 Git stash，已保留")
    refs = _git(path, "for-each-ref", "--format=%(objectname)", "refs/heads", "refs/tags")
    reflog = _git(path, "reflog", "--all", "--format=%H")
    for revision in set((refs + "\n" + reflog).splitlines()):
        if revision and revision.lower() != commit.lower():
            if not COMMIT.fullmatch(revision):
                raise CleanupError("工作区存在未确认 Git 历史，已保留")
            _git(path, "merge-base", "--is-ancestor", revision, commit)
    _input_cache(path, evidence)
    changes = _git(
        path, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored", "--ignore-submodules=none"
    )
    for change in changes.split("\0"):
        if not change:
            continue
        if change[:2] in ("??", "!!") and change[3:].startswith(".arena-inputs/"):
            continue
        raise CleanupError("存在未提交、未跟踪或忽略的文件，已保留")
    remote = _git(path, "config", "--get-all", "remote.origin.url")
    if not GITHUB.fullmatch(remote) or _git(path, "remote", "get-url", "--all", "origin") != remote:
        raise CleanupError("远端不是不含凭据的 HTTPS GitHub 地址，已保留")
    return size, remote


def _eligible(root, item, status, remote_verifier):
    path = Path(item["workspace"])
    _safe_path(path)
    _, receipt = _receipt(root, item)
    if receipt.get("status") != "complete_acknowledged":
        raise CleanupError("执行结果未确认，已保留")
    if status and status.get("status") in ("queued", "running", "in_review", "coordinating"):
        raise CleanupError("任务仍在排队、执行或验收中，已保留")
    if not status or status.get("status") != "done":
        raise CleanupError("服务器未确认此任务已经完成验收，已保留")
    if any(status.get(key) != item[key] for key in ("project_id", "attempt")):
        raise CleanupError("服务器任务版本与目录不匹配，已保留")
    if status.get("revision") != receipt.get("requirement_revision"):
        raise CleanupError("需求版本与回执不匹配，已保留")
    if (status.get("kind") == "publisher") != item["release"]:
        raise CleanupError("工作区类型与服务器记录不匹配，已保留")
    evidence, confirmed = receipt.get("evidence"), status.get("evidence")
    if not isinstance(evidence, dict) or not isinstance(confirmed, dict):
        raise CleanupError("缺少同步证据，已保留")
    for record in (evidence, confirmed):
        if (
            record.get("delivery") != "github"
            or record.get("execution_uncertain")
            or record.get("checkpoint")
            or record.get("push_status") == "unconfirmed"
            or record.get("merge_status") not in (None, "completed", "not_requested")
            or record.get("deployment_status") not in (None, "completed", "not_requested")
        ):
            raise CleanupError("成果仅在本地或存在未确认执行记录，已保留")
    commit = evidence.get("commit", "")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit) or not evidence.get("machine_id"):
        raise CleanupError("同步证据不完整，已保留")
    for key in ("commit", "machine_id", "workspace", "branch", "published_ref", "input_artifacts"):
        if evidence.get(key) != confirmed.get(key):
            raise CleanupError("本地回执与服务器成果不匹配，已保留")
    if evidence.get("workspace") != str(path):
        raise CleanupError("执行回执不指向此工作区，已保留")
    branch = evidence.get("published_ref") if item["release"] else evidence.get("branch")
    if not _branch(branch):
        raise CleanupError("缺少明确的远端分支，已保留")
    _, remote = _local_contents(path, commit, evidence)
    try:
        verified = remote_verifier(remote, branch, commit)
    except Exception:
        verified = False
    if verified is not True:
        raise CleanupError("无法确认记录的提交仍可从 GitHub 分支获取，已保留")
    size, final_remote = _local_contents(path, commit, evidence)
    if final_remote != remote:
        raise CleanupError("远端配置在检查期间变化，已保留")
    return {**item, "bytes": size, "commit": commit, "branch": branch, "reason": "已验收并确认 GitHub 同步"}


def plan(root, server_status, remote_verifier=None, *, keep=3):
    """Build a read-only plan; server_status is an authenticated, fresh status response."""
    root = _root(root)
    remote_verifier = remote_verifier or verify_remote
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise CleanupError("每个项目至少保留一份工作区，默认保留最近三份")
    rows = server_status.get("tasks", []) if isinstance(server_status, dict) else []
    statuses = {(row.get("id"), row.get("attempt")): row for row in rows if isinstance(row, dict)}
    items = sorted(inventory(root), key=lambda item: (item["updated"], item["workspace"]), reverse=True)
    counts, candidates, preserved = {}, [], []
    for item in items:
        project = item["project_id"]
        counts[project] = counts.get(project, 0) + 1
        if counts[project] <= keep:
            preserved.append({**item, "reason": f"保留项目最近 {keep} 份工作区"})
            continue
        try:
            candidates.append(_eligible(root, item, statuses.get((item["task_id"], item["attempt"])), remote_verifier))
        except (CleanupError, OSError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, CleanupError) else "工作区检查未完成，已保留"
            preserved.append({**item, "reason": reason})
    return {
        "schema_version": 1,
        "root": str(root),
        "keep": keep,
        "candidates": candidates,
        "preserved": preserved,
        "reclaimable_bytes": sum(item["bytes"] for item in candidates),
    }


def _save_receipt(path, receipt):
    _safe_path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            temporary.chmod(0o600)
            json.dump(receipt, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _remove_tree(path):
    def writable_retry(function, filename, _error):
        target = Path(filename)
        _safe_path(target)
        if not target.is_relative_to(path):
            raise CleanupError("删除路径已变化")
        target.chmod(target.stat().st_mode | stat.S_IWUSR)
        function(filename)

    _safe_path(path)
    shutil.rmtree(path, onerror=writable_retry)


def apply_plan(root, proposed, status_provider, remote_verifier=None):
    """Revalidate an explicit plan under the worker's exclusive process lock before deletion."""
    root = _root(root)
    with workspace_lock(root):
        return apply_plan_locked(root, proposed, status_provider, remote_verifier)


def apply_plan_locked(root, proposed, status_provider, remote_verifier=None):
    """Apply a plan when the caller already holds workspace_lock for this root."""
    root = _root(root)
    remote_verifier = remote_verifier or verify_remote
    if proposed.get("root") != str(root):
        raise CleanupError("清理计划不属于此工作区")
    current = plan(root, status_provider(inventory(root)), remote_verifier, keep=proposed.get("keep", 3))
    expected = {(item["workspace"], item["commit"]) for item in proposed.get("candidates", [])}
    removed, retained = [], []
    for item in current["candidates"]:
        if (item["workspace"], item["commit"]) not in expected:
            continue
        path = Path(item["workspace"])
        try:
            # Recheck local files after potentially slow network verification of other candidates.
            status = status_provider([item])
            rows = status.get("tasks", [])
            confirmed = next(
                (row for row in rows if row.get("id") == item["task_id"] and row.get("attempt") == item["attempt"]),
                None,
            )
            _eligible(root, item, confirmed, remote_verifier)
            receipt_path, receipt = _receipt(root, item)
            receipt["workspace_cleanup"] = {
                "status": "started",
                "started_at": time.time(),
                "commit": item["commit"],
            }
            _save_receipt(receipt_path, receipt)
            _remove_tree(path)
            receipt["workspace_cleanup"].update(status="removed", completed_at=time.time())
            _save_receipt(receipt_path, receipt)
            removed.append(item)
        except (CleanupError, OSError, ValueError):
            retained.append({**item, "reason": "删除未完成确认；已保留执行回执，请检查实际目录"})
    return {
        **current,
        "removed": removed,
        "retained": retained,
        "reclaimed_bytes": sum(i["bytes"] for i in removed),
    }


def _token(path):
    path = Path(path).expanduser().absolute()
    _safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise CleanupError("电脑凭据必须保存在本地普通文件中")
    if os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600:
        raise CleanupError("电脑凭据文件权限必须为 0600")
    value = path.read_text(encoding="utf-8").strip()
    if not 20 <= len(value) <= 200 or any(character.isspace() for character in value):
        raise CleanupError("电脑凭据文件格式无效")
    return value


def cleanup(config_path, token_file, *, apply=False, keep=3):
    """Inspect with the local machine credential; mutation requires apply=True."""
    from .worker import API, load_config

    try:
        config = load_config(config_path)
        token = _token(token_file)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        raise CleanupError("无法读取工作区配置或 0600 电脑凭据文件；未删除任何内容") from None
    root = _root(config["workspace_root"])
    api = API(config["server"], token, config.get("allow_http", False))

    def statuses(items):
        tasks = []
        for start in range(0, len(items), 100):
            batch = [{"id": item["task_id"], "attempt": item["attempt"]} for item in items[start : start + 100]]
            try:
                response = api.request("POST", "/api/machines/workspaces/status", {"tasks": batch})
                if not isinstance(response, dict) or not isinstance(response.get("tasks"), list):
                    raise CleanupError("服务器返回了无效工作区状态")
                tasks.extend(response["tasks"])
            except Exception:
                raise CleanupError("无法确认服务器任务状态，已保留全部工作区") from None
        return {"tasks": tasks}

    try:
        proposed = plan(root, statuses(inventory(root)), keep=keep)
        return apply_plan(root, proposed, statuses) if apply else proposed
    finally:
        api.close()


def main():
    parser = argparse.ArgumentParser(description="Preview verified workspace cleanup; deletion is opt-in")
    parser.add_argument("--config", required=True)
    parser.add_argument("--token-file", required=True, help="Local 0600 machine credential file")
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--apply", action="store_true", help="Recheck and delete only verified candidates")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                cleanup(args.config, args.token_file, apply=args.apply, keep=args.keep), ensure_ascii=False, indent=2
            )
        )
    except (CleanupError, OSError):
        parser.exit(1, "工作区清理检查未通过；执行端须已停止，配置、凭据和远端状态须可验证。\n")


if __name__ == "__main__":
    main()
