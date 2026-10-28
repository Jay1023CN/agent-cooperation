"""Local backup, restore and user-service file generation for PAL Arena."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import logging.handlers
import os
import plistlib
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from xml.etree import ElementTree

FORMAT_VERSION = 1
MAX_FILES = 10_000
MAX_MANIFEST = 2_000_000
DEFAULT_LIMIT = 5_000_000_000
REQUIRED_TABLES = {"projects", "tasks", "identities", "sessions", "machines", "artifacts", "events"}


class MaintenanceError(ValueError):
    pass


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def allowed_name(name: str) -> bool:
    return name in ("arena.db", "owner-token.txt") or bool(re.fullmatch(r"artifacts/[0-9a-f]{32}", name))


def regular_file(path: Path):
    if not path.is_file() or path.is_symlink():
        raise MaintenanceError(f"备份源必须是普通文件：{path.name}")


def database_check(connection: sqlite3.Connection):
    tables = {row[0] for row in connection.execute('SELECT name FROM sqlite_master WHERE type="table"')}
    if not REQUIRED_TABLES <= tables:
        raise MaintenanceError("数据库不是完整的 PAL Arena 数据库")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise MaintenanceError("数据库完整性检查失败")


def backup(data_dir: str | Path, destination: str | Path, max_bytes: int = DEFAULT_LIMIT) -> dict:
    """Create a credential-bearing 0600 archive; existing archives are never replaced."""
    source = Path(data_dir).expanduser().resolve()
    target = Path(destination).expanduser().absolute()
    if max_bytes <= 0:
        raise MaintenanceError("备份容量限制必须为正数")
    if target.exists() or target.is_symlink():
        raise MaintenanceError("备份目标已经存在，请使用新的文件名")
    regular_file(source / "arena.db")
    if (source / "artifacts").is_symlink():
        raise MaintenanceError("产物目录不能是符号链接")
    if target.is_relative_to(source):
        raise MaintenanceError("备份文件应放在数据目录以外")
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "application": "pal-arena",
        "format_version": FORMAT_VERSION,
        "created": time.time(),
        "contains_credentials": True,
        "encrypted": False,
        "files": [],
    }
    created = False
    try:
        with tempfile.TemporaryDirectory(prefix=".pal-arena-backup-", dir=source.parent) as temporary:
            temp_root = Path(temporary)
            temp_root.chmod(0o700)
            snapshot = temp_root / "arena.db"
            # Reserve writes only while snapshotting the database and linking immutable artifacts.
            # Compression then runs without blocking server writes or losing deleted artifacts.
            guard = sqlite3.connect((source / "arena.db").as_uri() + "?mode=rw", uri=True, timeout=20)
            try:
                guard.execute("BEGIN IMMEDIATE")
                reader = sqlite3.connect((source / "arena.db").as_uri() + "?mode=ro", uri=True)
                saved = sqlite3.connect(snapshot)
                try:
                    reader.backup(saved)
                    database_check(saved)
                    rows = list(saved.execute("SELECT id,size FROM artifacts ORDER BY id"))
                finally:
                    saved.close()
                    reader.close()
                entries = [("arena.db", snapshot)]
                if (source / "owner-token.txt").exists() or (source / "owner-token.txt").is_symlink():
                    regular_file(source / "owner-token.txt")
                    shutil.copyfile(source / "owner-token.txt", temp_root / "owner-token.txt")
                    entries.append(("owner-token.txt", temp_root / "owner-token.txt"))
                for identifier, expected_size in rows:
                    name = f"artifacts/{identifier}"
                    if not allowed_name(name):
                        raise MaintenanceError("数据库包含无效产物路径")
                    path = source / name
                    regular_file(path)
                    if path.stat().st_size != expected_size:
                        raise MaintenanceError("产物文件大小与数据库记录不一致")
                    staged = temp_root / name
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    os.link(path, staged, follow_symlinks=False)
                    entries.append((name, staged))
                guard.rollback()
            finally:
                guard.close()
            if len(entries) > MAX_FILES:
                raise MaintenanceError("备份文件数超过限制")
            total = 0
            for name, path in entries:
                regular_file(path)
                size = path.stat().st_size
                total += size
                if total > max_bytes:
                    raise MaintenanceError("备份内容超过指定容量限制")
                manifest["files"].append({"name": name, "size": size, "sha256": file_hash(path)})
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with (
                os.fdopen(descriptor, "wb") as output,
                zipfile.ZipFile(
                    output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True
                ) as archive,
            ):
                for name, path in entries:
                    archive.write(path, name)
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return {
            "path": str(target),
            "file_count": len(manifest["files"]),
            "content_bytes": total,
            "contains_credentials": True,
            "encrypted": False,
        }
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        if created:
            target.unlink(missing_ok=True)
        raise MaintenanceError("备份失败，请检查文件权限、磁盘空间及数据库状态") from exc
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise


def inspect_archive(archive: zipfile.ZipFile, max_bytes: int) -> dict:
    entries = archive.infolist()
    if len(entries) > MAX_FILES + 1:
        raise MaintenanceError("备份条目数超过限制")
    names = [entry.filename for entry in entries]
    if len(set(names)) != len(names) or names.count("manifest.json") != 1:
        raise MaintenanceError("备份包含重复条目或缺少清单")
    total = 0
    for entry in entries:
        if entry.filename != "manifest.json" and not allowed_name(entry.filename):
            raise MaintenanceError("备份包含不允许的文件路径")
        mode = (entry.external_attr >> 16) & 0o170000
        if entry.is_dir() or mode not in (0, stat.S_IFREG):
            raise MaintenanceError("备份包含链接或非普通文件")
        if entry.flag_bits & 1:
            raise MaintenanceError("不支持加密 ZIP；请先在本机解密为受保护的临时文件")
        total += entry.file_size
        if total > max_bytes + MAX_MANIFEST:
            raise MaintenanceError("备份解压后超过容量限制")
    manifest_info = archive.getinfo("manifest.json")
    if manifest_info.file_size > MAX_MANIFEST:
        raise MaintenanceError("备份清单过大")
    try:
        manifest = json.loads(archive.read(manifest_info))
    except (ValueError, RecursionError, UnicodeDecodeError) as exc:
        raise MaintenanceError("备份清单无法解析") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("application") != "pal-arena"
        or manifest.get("format_version") != FORMAT_VERSION
    ):
        raise MaintenanceError("不支持的备份格式或版本")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise MaintenanceError("备份清单文件列表无效")
    declared = {}
    content_bytes = 0
    for item in files:
        if not isinstance(item, dict):
            raise MaintenanceError("备份文件记录无效")
        name, size, checksum = item.get("name"), item.get("size"), item.get("sha256")
        if (
            not isinstance(name, str)
            or not allowed_name(name)
            or name in declared
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        ):
            raise MaintenanceError("备份文件记录无效")
        declared[name] = item
        content_bytes += size
    if content_bytes > max_bytes:
        raise MaintenanceError("备份内容超过恢复容量限制")
    if "arena.db" not in declared or set(declared) != set(names) - {"manifest.json"}:
        raise MaintenanceError("备份清单与归档文件不一致")
    for name, item in declared.items():
        if archive.getinfo(name).file_size != item["size"]:
            raise MaintenanceError("备份文件大小与清单不一致")
    return manifest


def restore(archive_path: str | Path, target_dir: str | Path, max_bytes: int = DEFAULT_LIMIT) -> dict:
    """Validate into staging, then publish to a new or empty directory; never overwrite live data."""
    source = Path(archive_path).expanduser().absolute()
    target_input = Path(target_dir).expanduser().absolute()
    if max_bytes <= 0:
        raise MaintenanceError("恢复容量限制必须为正数")
    regular_file(source)
    if target_input.is_symlink():
        raise MaintenanceError("恢复目标不能是符号链接")
    if target_input.exists() and (not target_input.is_dir() or any(target_input.iterdir())):
        raise MaintenanceError("恢复目标必须是新目录或空目录；请保留原数据以便回滚")
    target_input.parent.mkdir(parents=True, exist_ok=True)
    target = target_input.parent.resolve() / target_input.name
    stage = Path(tempfile.mkdtemp(prefix=".pal-arena-restore-", dir=target.parent))
    stage.chmod(0o700)
    try:
        with zipfile.ZipFile(source) as archive:
            manifest = inspect_archive(archive, max_bytes)
            for item in manifest["files"]:
                path = stage / item["name"]
                path.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with archive.open(item["name"]) as packed, os.fdopen(descriptor, "wb") as output:
                    while block := packed.read(1024 * 1024):
                        size += len(block)
                        if size > item["size"]:
                            raise MaintenanceError("备份解压内容超出清单声明")
                        digest.update(block)
                        output.write(block)
                if size != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise MaintenanceError("备份校验和不匹配，未恢复任何现有数据")
        database = sqlite3.connect(stage / "arena.db")
        try:
            database_check(database)
            artifact_ids = set()
            for identifier, expected_size in database.execute("SELECT id,size FROM artifacts"):
                name = f"artifacts/{identifier}"
                if not allowed_name(name):
                    raise MaintenanceError("恢复数据库包含无效产物路径")
                artifact = stage / name
                if not artifact.is_file() or artifact.stat().st_size != expected_size:
                    raise MaintenanceError("恢复数据库引用了缺失或大小错误的产物")
                artifact_ids.add(name)
            if artifact_ids != {item["name"] for item in manifest["files"] if item["name"].startswith("artifacts/")}:
                raise MaintenanceError("归档产物与恢复数据库记录不一致")
            # A snapshot cannot prove that external agent work stopped or that old sessions remain valid.
            interrupted = database.execute(
                'SELECT count(*) FROM tasks WHERE status IN ("queued","running","coordinating")'
            ).fetchone()[0]
            database.execute(
                'UPDATE tasks SET status="interrupted" WHERE status IN ("queued","running","coordinating")'
            )
            database.execute("UPDATE tasks SET lease_hash=NULL,lease_until=NULL")
            if "agent_hash" in {row[1] for row in database.execute("PRAGMA table_info(tasks)")}:
                database.execute("UPDATE tasks SET agent_hash=NULL")
            database.execute("DELETE FROM sessions")
            database.execute("UPDATE machines SET seen=0")
            database.commit()
            database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            database.execute("PRAGMA journal_mode=DELETE")
        finally:
            database.close()
        (stage / "artifacts").mkdir(exist_ok=True)
        if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
            raise MaintenanceError("恢复目标在校验期间被修改，已停止")
        if target.exists():
            target.rmdir()
        stage.rename(target)
        return {
            "path": str(target),
            "file_count": len(manifest["files"]),
            "interrupted_tasks": interrupted,
            "sessions_invalidated": True,
            "contains_credentials": True,
        }
    except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError) as exc:
        raise MaintenanceError("恢复失败；请检查归档完整性、文件权限及可用空间，原数据未被覆盖") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def absolute_path(value: str | Path, platform: str) -> str:
    text = str(value)
    if platform == "windows":
        if not PureWindowsPath(text).is_absolute():
            raise MaintenanceError("Windows 服务路径必须是带盘符或 UNC 的绝对路径")
        return str(PureWindowsPath(text))
    path = PurePosixPath(os.path.expanduser(text))
    if not path.is_absolute():
        raise MaintenanceError("服务路径必须是绝对路径")
    return str(path)


def generate_service(
    output_dir: str | Path,
    python: str | Path,
    *,
    platform: str,
    role: str = "server",
    data_dir: str | Path | None = None,
    worker_config: str | Path | None = None,
    token_file: str | Path | None = None,
    label: str | None = None,
    port: int = 8765,
    user_id: str | None = None,
    service_log: str | Path | None = None,
    packaged: bool | None = None,
) -> dict:
    """Generate reviewable user-service files without registering or starting a service."""
    if platform not in ("macos", "windows") or role not in ("server", "worker"):
        raise MaintenanceError("服务平台或角色无效")
    if not 1 <= port <= 65535:
        raise MaintenanceError("端口无效")
    label = label or f"local.pal-arena.{role}"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", label):
        raise MaintenanceError("服务标识只能包含字母、数字、点、下划线和短横线")
    output = Path(output_dir).expanduser().resolve()
    if service_log is None:
        if platform == "windows" and os.name != "nt":
            raise MaintenanceError("跨系统生成 Windows 服务时需要明确指定 Windows service_log 路径")
        service_log = output / "logs" / f"{role}.log"
    packaged = bool(getattr(sys, "frozen", False)) if packaged is None else packaged
    entry = ["maintenance"] if packaged else ["-m", "arena.maintenance"]
    arguments = [
        absolute_path(python, platform),
        *entry,
        "run-service",
        "--role",
        role,
        "--log-file",
        absolute_path(service_log, platform),
    ]
    if role == "server":
        if data_dir is None:
            raise MaintenanceError("服务端需要明确的数据目录")
        arguments.extend(["--data", absolute_path(data_dir, platform), "--port", str(port)])
    else:
        if worker_config is None or token_file is None:
            raise MaintenanceError("工作机服务需要配置路径和本机令牌文件路径")
        arguments.extend(
            ["--config", absolute_path(worker_config, platform), "--token-file", absolute_path(token_file, platform)]
        )
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    extension = "plist" if platform == "macos" else "xml"
    service = output / f"{label}.{extension}"
    commands = output / f"{label}.commands.txt"
    if service.exists() or commands.exists():
        raise MaintenanceError("服务配置文件已存在，请选择新输出目录或标识")
    if platform == "macos":
        bootstrap_log = str(output / "logs" / f"{role}-bootstrap.log")
        (output / "logs").mkdir(exist_ok=True)
        content = plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": arguments,
                "RunAtLoad": True,
                "KeepAlive": True,
                "ThrottleInterval": 10,
                "ProcessType": "Interactive",
                "StandardOutPath": bootstrap_log,
                "StandardErrorPath": bootstrap_log,
                "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PATH": os.environ.get("PATH", os.defpath)},
            },
            sort_keys=False,
        )
        filename = f"{label}.plist"
        destination = f'"$HOME/Library/LaunchAgents/{filename}"'
        notes = "\n".join(
            [
                "# Run these commands yourself to register and start this user service.",
                'mkdir -p "$HOME/Library/LaunchAgents"',
                f"cp {shlex.quote(str(service))} {destination}",
                f'launchctl bootstrap "gui/$(id -u)" {destination}',
                "",
                "# Stop and unregister:",
                f'launchctl bootout "gui/$(id -u)" {destination}',
                f"rm {destination}",
                "",
            ]
        )
    else:
        user_id = user_id or (os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")).strip("\\")
        if not user_id or any(c in user_id for c in "\r\n\x00"):
            raise MaintenanceError("Windows 任务需要当前登录用户的 user_id")
        namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        ElementTree.register_namespace("", namespace)

        def element(parent, name, text=None, **attrs):
            child = ElementTree.SubElement(parent, f"{{{namespace}}}{name}", attrs)
            if text is not None:
                child.text = text
            return child

        task = ElementTree.Element(f"{{{namespace}}}Task", {"version": "1.4"})
        element(element(task, "RegistrationInfo"), "Description", f"PAL Arena {role}; current user only")
        trigger = element(element(task, "Triggers"), "LogonTrigger")
        element(trigger, "Enabled", "true")
        element(trigger, "UserId", user_id)
        principal = element(element(task, "Principals"), "Principal", id="Author")
        element(principal, "UserId", user_id)
        element(principal, "LogonType", "InteractiveToken")
        element(principal, "RunLevel", "LeastPrivilege")
        settings = element(task, "Settings")
        for name, value in {
            "MultipleInstancesPolicy": "IgnoreNew",
            "DisallowStartIfOnBatteries": "false",
            "StopIfGoingOnBatteries": "false",
            "StartWhenAvailable": "true",
            "ExecutionTimeLimit": "PT0S",
        }.items():
            element(settings, name, value)
        restart = element(settings, "RestartOnFailure")
        element(restart, "Interval", "PT1M")
        element(restart, "Count", "3")
        executable = element(element(task, "Actions", Context="Author"), "Exec")
        element(executable, "Command", arguments[0])
        element(executable, "Arguments", subprocess.list2cmdline(arguments[1:]))
        content = ElementTree.tostring(task, encoding="utf-16", xml_declaration=True)
        ps_path = str(service).replace("'", "''")
        notes = "\n".join(
            [
                "# Run in PowerShell as this Windows user; registration does not require a system service.",
                f"Register-ScheduledTask -TaskName '{label}' -Xml (Get-Content -Raw -LiteralPath '{ps_path}')",
                f"Start-ScheduledTask -TaskName '{label}'",
                "",
                "# Stop and unregister:",
                f"Stop-ScheduledTask -TaskName '{label}'",
                f"Unregister-ScheduledTask -TaskName '{label}' -Confirm:$false",
                "",
            ]
        )
    for path, value in ((service, content), (commands, notes.encode("utf-8"))):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(value)
    return {"service_file": str(service), "commands_file": str(commands), "installed": False, "role": role}


class LogStream(io.TextIOBase):
    def __init__(self, path):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
        path.chmod(0o600)
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))

    def write(self, value):
        if value.strip():
            self.handler.emit(logging.LogRecord("arena.service", logging.INFO, "", 0, value.rstrip("\n"), (), None))
        return len(value)

    def flush(self):
        self.handler.flush()


def run_service(args):
    stream = LogStream(args.log_file)
    with contextlib.ExitStack() as stack:
        stack.callback(stream.handler.close)
        stack.enter_context(contextlib.redirect_stdout(stream))
        stack.enter_context(contextlib.redirect_stderr(stream))
        if args.role == "worker":
            if not args.config or not args.token_file:
                raise MaintenanceError("工作机服务需要配置和令牌文件")
            path = Path(args.token_file).expanduser()
            regular_file(path)
            if os.name != "nt" and path.stat().st_mode & 0o077:
                raise MaintenanceError("工作机令牌文件必须仅当前用户可读写，请设置 chmod 600")
            if path.stat().st_size > 1024:
                raise MaintenanceError("工作机令牌文件大小无效")
            token = path.read_text(encoding="utf-8").strip()
            if not 20 <= len(token) <= 200:
                raise MaintenanceError("工作机令牌格式无效")
            from .worker import Worker, load_config

            Worker(load_config(args.config), token=token).run()
        else:
            import uvicorn

            from .api import create_app

            uvicorn.run(
                create_app(Path(args.data).expanduser()),
                host="127.0.0.1",
                port=args.port,
                proxy_headers=True,
                forwarded_allow_ips=os.environ.get("ARENA_TRUSTED_PROXIES", "127.0.0.1"),
            )


def main():
    parser = argparse.ArgumentParser(description="Local PAL Arena backup, restore and service tools")
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("backup")
    export.add_argument("--data", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--max-gb", type=float, default=5)
    recover = sub.add_parser("restore")
    recover.add_argument("--archive", required=True)
    recover.add_argument("--target", required=True)
    recover.add_argument("--max-gb", type=float, default=5)
    service = sub.add_parser("service")
    service.add_argument("--output", required=True)
    service.add_argument("--platform", choices=["macos", "windows"], required=True)
    service.add_argument("--python", default=sys.executable)
    service.add_argument("--role", choices=["server", "worker"], default="server")
    service.add_argument("--data")
    service.add_argument("--config")
    service.add_argument("--token-file")
    service.add_argument("--log-file")
    service.add_argument("--user-id")
    service.add_argument("--label")
    service.add_argument("--port", type=int, default=8765)
    service.add_argument("--packaged", action="store_true", default=None)
    run = sub.add_parser("run-service")
    run.add_argument("--role", choices=["server", "worker"], required=True)
    run.add_argument("--data", default="~/.pal-arena/server")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--config")
    run.add_argument("--token-file")
    run.add_argument("--log-file", required=True)
    args = parser.parse_args()
    try:
        if args.action == "backup":
            result = backup(args.data, args.output, max_bytes=int(args.max_gb * 1_000_000_000))
        elif args.action == "restore":
            result = restore(args.archive, args.target, max_bytes=int(args.max_gb * 1_000_000_000))
        elif args.action == "service":
            result = generate_service(
                args.output,
                args.python,
                platform=args.platform,
                role=args.role,
                data_dir=args.data,
                worker_config=args.config,
                token_file=args.token_file,
                service_log=args.log_file,
                user_id=args.user_id,
                label=args.label,
                port=args.port,
                packaged=args.packaged,
            )
        else:
            run_service(args)
            return
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.action == "backup":
            print("备份包含身份和本机登录令牌，未加密。请仅存放在受保护的位置；不要上传到 GitHub。", file=sys.stderr)
    except (MaintenanceError, OSError) as exc:
        parser.exit(1, f"PAL Arena maintenance: {exc}\n")


if __name__ == "__main__":
    main()
