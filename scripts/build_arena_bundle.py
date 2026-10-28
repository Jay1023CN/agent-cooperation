"""Build and smoke-test a native bundle for this machine's actual OS/architecture."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

PYINSTALLER_VERSION = "6.22.3"


class BundleError(RuntimeError):
    pass


class StaticResources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "script" and "src" in attributes:
            self.urls.append(attributes["src"])
        elif tag == "link" and attributes.get("rel") == "stylesheet" and "href" in attributes:
            self.urls.append(attributes["href"])


def command_output(argv, cwd):
    result = subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def source_metadata(source: Path, *, allow_dirty=False):
    commit = command_output(["git", "rev-parse", "HEAD"], source)
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise BundleError("Source must resolve to an immutable Git commit")
    dirty = bool(command_output(["git", "status", "--porcelain", "--untracked-files=normal"], source))
    if dirty and not allow_dirty:
        raise BundleError(
            "Commit source changes before building a delivery bundle (use --allow-dirty only for local QA)"
        )
    packages = {}
    for name in ("pal-mcp-server", "mcp", "fastapi", "uvicorn", "pydantic", "httpx", "psutil", "pyinstaller"):
        packages[name] = importlib.metadata.version(name)
    return {"commit": commit, "dirty": dirty, "packages": packages}


def payload_hashes(root: Path):
    values = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if name in ("manifest.json", "SHA256SUMS"):
            continue
        if path.is_symlink():
            target = os.readlink(path)
            if not path.resolve().is_relative_to(root.resolve()):
                raise BundleError("Bundle contains a symlink outside its own directory")
            values[name] = {"type": "symlink", "target": target}
        elif path.is_dir():
            continue
        elif path.is_file():
            values[name] = {"type": "file", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            raise BundleError(f"Unsupported bundle member: {name}")
    return values


def stage_bundle(binary_dir: Path, destination: Path, source: Path, metadata: dict, source_archive: Path | None = None):
    if destination.exists():
        raise BundleError("Bundle staging directory already exists")
    system = platform.system().lower()
    executable = "pal-arena.exe" if system == "windows" else "pal-arena"
    if not (binary_dir / executable).is_file():
        raise BundleError("Native executable is missing")
    destination.mkdir(parents=True)
    shutil.copytree(binary_dir, destination / "pal-arena", symlinks=True)
    if source_archive is not None:
        shutil.copy2(source_archive, destination / "source.tar.gz")
    for filename in ("LICENSE", "NOTICE"):
        if (source / filename).is_file():
            shutil.copy2(source / filename, destination / filename)
    for document in sorted((source / "docs").glob("arena*.md")):
        if document.is_file():
            (destination / "docs").mkdir(exist_ok=True)
            shutil.copy2(document, destination / "docs" / document.name)
    if system == "windows":
        (destination / "Start Server.cmd").write_bytes(
            b'@echo off\r\nsetlocal\r\nset PYTHONUTF8=1\r\n"%~dp0pal-arena\\pal-arena.exe" serve\r\nif errorlevel 1 pause\r\n'
        )
        (destination / "Check Installation.cmd").write_bytes(
            b'@echo off\r\nsetlocal\r\nset PYTHONUTF8=1\r\n"%~dp0pal-arena\\pal-arena.exe" doctor\r\npause\r\n'
        )
        binary_command = r".\pal-arena\pal-arena.exe"
    else:
        for name, command in (("Start Server.command", "serve"), ("Check Installation.command", "doctor")):
            file = destination / name
            file.write_text(
                '#!/bin/bash\nset -e\nTASK_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"\n'
                f'exec "$TASK_DIR/pal-arena/pal-arena" {command}\n',
                encoding="utf-8",
            )
            file.chmod(0o755)
        binary_command = "./pal-arena/pal-arena"
    readme = f"""PAL Arena 本机运行包

平台：{platform.system()} / {platform.machine()}
源码：{metadata['commit']}
状态：{'本地测试包，包含未提交修改，不能作为正式交付凭证' if metadata['dirty'] else '已提交源码构建'}
签名：本包未申请 Apple Developer / Windows Authenticode 签名。

1. 把整个解压目录保存在固定位置；不要只移动里面的单个程序。
2. 双击 Start Server 启动中心，打开 http://127.0.0.1:8765。
3. 首次登录令牌保存在用户目录 ~/.pal-arena/server/owner-token.txt；程序只显示文件位置。
4. 可以先双击 Check Installation 查看内置运行时、Git、客户端和连接条件的自检。
5. Mac/Windows 上已有的代理软件、Git、Tailscale 和 Blender 仍需按实际任务安装并登录。

本包已包含 Python 与平台依赖，无需安装系统 Python；第三方 agent 的订阅和授权不包含在本包中。
系统可能对未签名的下载应用进行检查。不要关闭系统安全功能；如需组织签名，请签名后重新校验交付包。

执行端命令：
{binary_command} worker --config /absolute/path/to/worker.json

直接运行 worker 时，从网页创建该电脑的机器令牌，并设置 ARENA_MACHINE_TOKEN 环境变量。
令牌文件启动使用 maintenance run-service --role worker --token-file ...，详见 docs/arena-delivery.md。
项目数据默认保存在用户目录 ~/.pal-arena/server，工作目录默认 ~/.pal-arena/workspaces，不放在程序目录。
更新时先退出旧进程，保留用户数据并备份，把新包解压到另一个目录后启动。不要覆盖运行中的程序目录。

备份和启动服务：
{binary_command} maintenance --help

更多说明见 docs/arena.md。manifest.json 记录源码和依赖版本，SHA256SUMS 用于核对文件完整性。
正式交付包的 source.tar.gz 保存该提交的完整源码；本地未提交版本的测试包不会附带不匹配的源码存档。
真实订阅客户端和四机任务验收，需要在用户设备上完成。示例预览不会连接真实 agent。
"""
    (destination / "READ-ME.txt").write_text(readme, encoding="utf-8")
    hashes = payload_hashes(destination)
    manifest = {
        "format": 1,
        "product": "PAL Arena",
        "platform": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "source_commit": metadata["commit"],
        "source_dirty": metadata["dirty"],
        "signing": "unsigned",
        "packages": metadata["packages"],
        "files": hashes,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f'{v["sha256"]}  {k}' for k, v in hashes.items() if v["type"] == "file"]
    lines.append(hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "  manifest.json")
    (destination / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination / "pal-arena" / executable


def archive_bundle(staging: Path, output: Path):
    checksum_path = output.with_name(output.name + ".sha256")
    if output.exists() or checksum_path.exists():
        raise BundleError("Refusing to replace an existing delivery archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".arena-archive-", dir=output.parent) as directory:
        temporary = Path(directory) / "payload"
        if output.name.endswith(".zip"):
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(staging.rglob("*")):
                    if file.is_symlink():
                        raise BundleError("Use tar.gz for native bundles containing symlinks")
                    if file.is_file():
                        archive.write(file, (Path(staging.name) / file.relative_to(staging)).as_posix())
        elif output.name.endswith(".tar.gz"):
            with tarfile.open(temporary, "w:gz", dereference=False) as archive:
                archive.add(staging, arcname=staging.name)
        else:
            raise BundleError("Unsupported bundle archive format")
        checksum = hashlib.sha256(temporary.read_bytes()).hexdigest()
        # A completed archive becomes visible atomically, without replacing an existing file.
        try:
            os.link(temporary, output)
        except FileExistsError:
            raise BundleError("Refusing to replace an existing delivery archive") from None
        with checksum_path.open("x", encoding="ascii") as file:
            file.write(checksum + "  " + output.name + "\n")
    return output


async def smoke_mcp(executable: Path, server_url: str, command="internal-mcp", extra_args=None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "PATH": "", "ARENA_SERVER": server_url, "ARENA_RUN_TOKEN": "build-smoke-only-no-task"}
    parameters = StdioServerParameters(command=str(executable), args=[command, *(extra_args or [])], env=env)
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            required = {
                "internal-mcp": {"context", "create_task", "report_review", "finish_project"},
                "internal-agent-link": {"agent_create", "agent_send", "agent_read"},
                "internal-desktop-mcp": {"next_task", "read_file", "write_file", "start_command", "submit_result"},
            }[command]
            if not required <= {t.name for t in result.tools}:
                raise BundleError("Native MCP entry point did not expose the expected platform tools")
        error_log.seek(0)
        details = error_log.read()
        if "Traceback (most recent call last)" in details:
            raise BundleError(f"Native MCP entry point reported a runtime exception:\n{details[-6000:]}")


def smoke_posix_supervisor(executable: Path):
    if os.name == "nt":
        return
    from unittest.mock import patch

    from agent_link.process_owner import spawn_owned, stop_owned

    git = shutil.which("git")
    if not git:
        raise BundleError("Git is required for the native supervisor smoke test")
    with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)):
        process = spawn_owned(
            [git, "--version"],
            cwd=executable.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            output = process.stdout.read().decode("utf-8", errors="replace")
            if process.wait(timeout=10) != 0 or "git version" not in output.lower():
                raise BundleError("Native process supervisor did not return the child output and status")
        finally:
            stop_owned(process)
            process.stdout.close()
            process.stderr.close()
        marker = "/__pal_arena_loader_probe__"
        process = spawn_owned(
            ["/usr/bin/env"],
            cwd=executable.parent,
            env={"PATH": os.environ.get("PATH", ""), "LD_LIBRARY_PATH": marker},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            environment = process.stdout.read().decode("utf-8", errors="replace")
            if process.wait(timeout=10) != 0 or f"LD_LIBRARY_PATH={marker}" not in environment.splitlines():
                raise BundleError("Native supervisor did not restore the child loader environment")
            if "_PAL_SUPERVISOR_" in environment:
                raise BundleError("Native supervisor leaked internal environment markers to its child")
        finally:
            stop_owned(process)
            process.stdout.close()
            process.stderr.close()


def smoke_bundle(executable: Path):
    for arguments in (
        ["--help"],
        ["worker", "--help"],
        ["maintenance", "--help"],
        ["internal-agent-link", "--help"],
        ["internal-desktop-mcp", "--help"],
    ):
        command_output([str(executable), *arguments], executable.parent)
    diagnostic = json.loads(command_output([str(executable), "doctor", "--no-network", "--json"], executable.parent))
    if diagnostic.get("status") not in ("ok", "needs_attention"):
        raise BundleError("Native installation check failed")
    command_output([str(executable), "internal-exec", "git", "--version"], executable.parent)
    smoke_posix_supervisor(executable)
    with tempfile.TemporaryDirectory(prefix="arena-native-smoke-") as directory:
        root = Path(directory)
        config = root / "worker config 中文.json"
        command_output([str(executable), "worker-config", "--name", "天选 6 Pro", "--output", str(config)], root)
        if json.loads(config.read_text(encoding="utf-8")).get("name") != "天选 6 Pro":
            raise BundleError("Native worker configuration did not preserve UTF-8 text")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (root / "server.log").open("wb") as log:
            process = subprocess.Popen(
                [str(executable), "serve", "--data", str(root / "data"), "--port", str(port)],
                cwd=root,
                env={**os.environ, "PATH": ""},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 40
                while True:
                    try:
                        with opener.open(url + "/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except (urllib.error.URLError, TimeoutError):
                        if process.poll() is not None or time.monotonic() >= deadline:
                            log.flush()
                            details = (root / "server.log").read_text(encoding="utf-8", errors="replace")[-6000:]
                            raise BundleError(f"Native server failed its startup smoke test:\n{details}") from None
                        time.sleep(0.2)
                with opener.open(url + "/", timeout=5) as response:
                    page = response.read().decode("utf-8")
                if "PAL Arena" not in page:
                    raise BundleError("Native server did not serve the platform application")
                resources = StaticResources()
                resources.feed(page)
                if not resources.urls:
                    raise BundleError("Native server is missing its static application resources")
                for resource in resources.urls:
                    if not resource.startswith("/static/"):
                        raise BundleError("Native application must load scripts and styles from its own bundle")
                    with opener.open(url + resource, timeout=5) as response:
                        if not response.read():
                            raise BundleError("Native server returned an empty application resource")
                asyncio.run(asyncio.wait_for(smoke_mcp(executable, url), timeout=25))
                asyncio.run(asyncio.wait_for(smoke_mcp(executable, url, "internal-agent-link"), timeout=25))
                desktop_config = root / "desktop config 中文.json"
                desktop_token = root / "desktop token 中文.txt"
                desktop_config.write_text(
                    json.dumps(
                        {
                            "server": url,
                            "name": "Desktop smoke",
                            "slots": 1,
                            "workspace_root": str(root / "desktop workspace"),
                            "agents": [{"key": "gui", "name": "GUI", "provider": "desktop_mcp", "roles": ["worker"]}],
                        }
                    ),
                    encoding="utf-8",
                )
                desktop_token.write_text("test-only-no-task", encoding="utf-8")
                args = ["--config", str(desktop_config), "--token-file", str(desktop_token)]
                output = json.loads(
                    command_output([str(executable), "internal-desktop-mcp", *args, "--print-mcp-config"], root)
                )
                if "test-only-no-task" in json.dumps(output) or "pal-arena-desktop" not in output["mcpServers"]:
                    raise BundleError("Desktop MCP configuration exposed its token or was missing")
                asyncio.run(asyncio.wait_for(smoke_mcp(executable, url, "internal-desktop-mcp", args), timeout=25))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def build_bundle(source: Path, output: Path, *, allow_dirty=False):
    metadata = source_metadata(source, allow_dirty=allow_dirty)
    if metadata["packages"]["pyinstaller"] != PYINSTALLER_VERSION:
        raise BundleError(f"Build with pyinstaller=={PYINSTALLER_VERSION}")
    suffix = "zip" if platform.system() == "Windows" else "tar.gz"
    name = f'pal-arena-{platform.system().lower()}-{platform.machine().lower()}-{metadata["commit"][:12]}'
    if metadata["dirty"]:
        name += "-local-qa"
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / (name + "." + suffix)
    if archive_path.exists():
        raise BundleError("Build output already exists; choose a new output directory")
    with tempfile.TemporaryDirectory(prefix="arena-native-build-") as work:
        work = Path(work)
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onedir",
            "--name",
            "pal-arena",
            "--distpath",
            str(work / "dist"),
            "--workpath",
            str(work / "work"),
            "--specpath",
            str(work / "spec"),
            "--paths",
            str(source),
            "--collect-all",
            "arena",
            "--collect-data",
            "mcp",
            "--collect-submodules",
            "mcp.server",
            "--copy-metadata",
            "mcp",
            "--collect-all",
            "uvicorn",
            "--collect-submodules",
            "agent_link",
            "--copy-metadata",
            "pal-mcp-server",
            "--copy-metadata",
            "fastapi",
            "--copy-metadata",
            "pydantic",
            "--copy-metadata",
            "httpx",
            "--copy-metadata",
            "psutil",
            str(source / "scripts" / "arena_launcher.py"),
        ]
        subprocess.run(command, cwd=source, check=True)
        source_archive = None
        if not metadata["dirty"]:
            source_archive = work / "source.tar.gz"
            subprocess.run(
                ["git", "archive", "--format=tar.gz", f"--output={source_archive}", metadata["commit"]],
                cwd=source,
                check=True,
            )
        # Exercise downloaded/extracted locations containing both spaces and non-ASCII text.
        staging = work / "PAL Arena 安装测试" / name
        executable = stage_bundle(work / "dist" / "pal-arena", staging, source, metadata, source_archive)
        smoke_bundle(executable)
        return archive_bundle(staging, archive_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path, default=Path("dist/arena-delivery"))
    parser.add_argument("--allow-dirty", action="store_true", help="Local QA only; manifest records uncommitted source")
    args = parser.parse_args()
    try:
        archive = build_bundle(args.source.resolve(), args.output.resolve(), allow_dirty=args.allow_dirty)
    except (BundleError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Arena packaging failed: {exc}\n")
    print(f"Verified native bundle: {archive}")


if __name__ == "__main__":
    main()
