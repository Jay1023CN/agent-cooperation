import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("arena_bundle_build", SCRIPTS / "build_arena_bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)
spec = importlib.util.spec_from_file_location("arena_native_launcher", SCRIPTS / "arena_launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.mark.parametrize(
    ("argv", "module", "expected"),
    [
        (["pal-arena"], "arena.cli", ["pal-arena", "serve"]),
        (["pal-arena", "serve", "--port", "8766"], "arena.cli", ["pal-arena", "serve", "--port", "8766"]),
        (["pal-arena", "worker", "--config", "x.json"], "arena.worker", ["pal-arena", "--config", "x.json"]),
        (["pal-arena", "maintenance", "backup"], "arena.maintenance", ["pal-arena", "backup"]),
        (["pal-arena", "internal-mcp"], "arena.mcp", ["pal-arena"]),
        (["pal-arena", "internal-agent-link", "--check"], "agent_link.server", ["pal-arena", "--check"]),
        (
            ["pal-arena", "internal-desktop-mcp", "--config", "x.json", "--token-file", "token.txt"],
            "arena.desktop_mcp",
            ["pal-arena", "--config", "x.json", "--token-file", "token.txt"],
        ),
    ],
)
def test_native_launcher_routes_without_python_module_flags(monkeypatch, argv, module, expected):
    fake = ModuleType(module)
    received = []
    fake.main = lambda: received.append(list(sys.argv))
    monkeypatch.setitem(sys.modules, module, fake)
    monkeypatch.setattr(sys, "argv", argv)
    launcher.main()
    assert received == [expected]


def test_native_launcher_dispatches_process_supervisor(monkeypatch):
    fake = ModuleType("agent_link.process_owner")
    received = []
    fake.supervise = lambda status, control, command: received.append((status, control, command)) or 0
    monkeypatch.setitem(sys.modules, "agent_link.process_owner", fake)
    monkeypatch.setattr(sys, "argv", ["pal-arena", "internal-supervisor", "7", "8", "git", "--version"])
    with pytest.raises(SystemExit) as result:
        launcher.main()
    assert result.value.code == 0
    assert received == [(7, 8, ["git", "--version"])]


def test_native_redirected_output_is_utf8_even_with_legacy_codepage(monkeypatch):
    raw_output, raw_error = io.BytesIO(), io.BytesIO()
    stdout = io.TextIOWrapper(raw_output, encoding="cp1252")
    stderr = io.TextIOWrapper(raw_error, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    launcher.configure_output()
    print("天选 6 Pro")
    print("电脑检查", file=sys.stderr)
    stdout.flush()
    stderr.flush()
    assert raw_output.getvalue().decode("utf-8").strip() == "天选 6 Pro"
    assert raw_error.getvalue().decode("utf-8").strip() == "电脑检查"


def test_native_external_broker_preserves_spaces_unicode_and_exit_status(tmp_path):
    script = tmp_path / "program with 空格.py"
    script.write_text(
        "import sys\nassert sys.argv[1] == 'value with 中文 spaces'\nraise SystemExit(7)\n", encoding="utf-8"
    )
    assert launcher.run_external([sys.executable, str(script), "value with 中文 spaces"]) == 7
    assert launcher.run_external([str(tmp_path / "missing-executable")]) == 127
    assert launcher.run_external([]) == 2


def test_external_broker_restores_system_library_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path / "bundle"))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system-libraries")
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "bundle" / "bin"), "/system-bin"]))
    env = launcher.external_environment()
    assert env["LD_LIBRARY_PATH"] == "/system-libraries"
    assert env["PATH"] == "/system-bin"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    assert "LD_LIBRARY_PATH" not in launcher.external_environment()


def test_windows_external_broker_clears_dll_directory_in_its_own_process(monkeypatch):
    import ctypes

    calls = []

    def clear(value):
        calls.append(("clear", value))
        return 1

    def spawn(argv, *, env, shell):
        calls.append(("spawn", argv, shell))
        return 23

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(SetDllDirectoryW=clear)), raising=False
    )
    monkeypatch.setattr(launcher.subprocess, "call", spawn)
    assert launcher.run_external(["agent.exe", "prompt with spaces"]) == 23
    assert calls == [("clear", None), ("spawn", ["agent.exe", "prompt with spaces"], False)]


def test_native_mcp_stream_wrapper_cannot_close_interpreter_stdout():
    script = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
def transport():
    sys.stdin.buffer.close()
    sys.stdout.buffer.close()
launcher.run_stdio(transport)
print('stdout survived')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(SCRIPTS / "arena_launcher.py")],
        input="",
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "stdout survived"
    assert result.stderr == ""


def test_native_resource_smoke_follows_vite_hashed_assets():
    resources = bundle.StaticResources()
    resources.feed(
        '<html><link rel="stylesheet" href="/static/assets/index-123.css">'
        '<script type="module" src="/static/assets/index-456.js"></script>'
        '<a href="https://example.test/docs">Docs</a></html>'
    )
    assert resources.urls == ["/static/assets/index-123.css", "/static/assets/index-456.js"]


def fake_distribution(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "LICENSE").write_text("Apache license\n", encoding="utf-8")
    (source / "docs").mkdir()
    (source / "docs" / "arena.md").write_text("Install instructions\n", encoding="utf-8")
    (source / "docs" / "arena-maintenance.md").write_text("Backup instructions\n", encoding="utf-8")
    dist = tmp_path / "dist"
    dist.mkdir()
    executable = "pal-arena.exe" if os.name == "nt" else "pal-arena"
    (dist / executable).write_bytes(b"native-fixture")
    (dist / "_internal").mkdir()
    (dist / "_internal" / "library.bin").write_bytes(b"support-files")
    metadata = {"commit": "a" * 40, "dirty": False, "packages": {"pal-mcp-server": "9.9.1"}}
    return source, dist, metadata


def test_distribution_includes_runtime_launchers_sources_and_checksums(tmp_path):
    source, dist, metadata = fake_distribution(tmp_path)
    target = tmp_path / "bundle"
    executable = bundle.stage_bundle(dist, target, source, metadata)
    assert executable.is_file()
    assert (target / "pal-arena" / "_internal" / "library.bin").read_bytes() == b"support-files"
    assert (target / "LICENSE").read_text() == "Apache license\n"
    assert (target / "docs" / "arena-maintenance.md").read_text() == "Backup instructions\n"
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["source_commit"] == "a" * 40
    assert manifest["source_dirty"] is False
    assert manifest["architecture"] == bundle.platform.machine()
    assert manifest["signing"] == "unsigned"
    assert "owner-token" not in manifest["files"]
    for name, entry in manifest["files"].items():
        assert entry["sha256"] == hashlib.sha256((target / name).read_bytes()).hexdigest()
    launch = target / ("Start Server.cmd" if os.name == "nt" else "Start Server.command")
    assert launch.is_file()
    assert "serve" in launch.read_text()
    assert "--data" not in launch.read_text()
    assert "ARENA_MACHINE_TOKEN=" not in launch.read_text()
    assert b"\r\r\n" not in launch.read_bytes()
    assert "无需安装系统 Python" in (target / "READ-ME.txt").read_text(encoding="utf-8")


def test_staging_never_overwrites_existing_application_directory(tmp_path):
    source, dist, metadata = fake_distribution(tmp_path)
    target = tmp_path / "existing"
    target.mkdir()
    (target / "user-data").write_text("important")
    with pytest.raises(bundle.BundleError, match="already exists"):
        bundle.stage_bundle(dist, target, source, metadata)
    assert (target / "user-data").read_text() == "important"


def test_archive_preserves_start_script_and_has_independent_checksum(tmp_path):
    source, dist, metadata = fake_distribution(tmp_path)
    staging = tmp_path / "bundle"
    bundle.stage_bundle(dist, staging, source, metadata)
    output = tmp_path / "delivery.tar.gz"
    bundle.archive_bundle(staging, output)
    with tarfile.open(output) as archive:
        names = archive.getnames()
        assert "bundle/manifest.json" in names
        assert "bundle/docs/arena.md" in names
        if os.name != "nt":
            assert archive.getmember("bundle/Start Server.command").mode & 0o111
    assert (
        output.with_name(output.name + ".sha256")
        .read_text()
        .startswith(hashlib.sha256(output.read_bytes()).hexdigest())
    )
    with pytest.raises(bundle.BundleError, match="replace"):
        bundle.archive_bundle(staging, output)


@pytest.mark.parametrize("directory", [False, True])
def test_bundle_rejects_external_library_symlinks(tmp_path, directory):
    if os.name == "nt":
        pytest.skip("Creating symbolic links requires a Windows privilege")
    source, dist, metadata = fake_distribution(tmp_path)
    outside = tmp_path / "secret-file"
    if directory:
        outside.mkdir()
    else:
        outside.write_text("private")
    (dist / "escape").symlink_to(outside, target_is_directory=directory)
    with pytest.raises(bundle.BundleError, match="outside"):
        bundle.stage_bundle(dist, tmp_path / "bundle", source, metadata)


def test_archive_failure_does_not_leave_a_deliverable(tmp_path):
    if os.name == "nt":
        pytest.skip("Creating symbolic links requires a Windows privilege")
    source, dist, metadata = fake_distribution(tmp_path)
    (dist / "linked-library").symlink_to("_internal/library.bin")
    staging = tmp_path / "bundle"
    bundle.stage_bundle(dist, staging, source, metadata)
    output = tmp_path / "delivery.zip"
    with pytest.raises(bundle.BundleError, match="symlinks"):
        bundle.archive_bundle(staging, output)
    assert not output.exists()
    assert not output.with_name(output.name + ".sha256").exists()


def test_source_archive_is_embedded_and_checksummed(tmp_path):
    source, dist, metadata = fake_distribution(tmp_path)
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"pinned-source-fixture")
    target = tmp_path / "bundle"
    bundle.stage_bundle(dist, target, source, metadata, archive)
    manifest = json.loads((target / "manifest.json").read_text())
    assert (target / "source.tar.gz").read_bytes() == archive.read_bytes()
    assert manifest["files"]["source.tar.gz"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_source_provenance_refuses_uncommitted_code_by_default(tmp_path, monkeypatch):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "file.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "file.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-m", "source"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(bundle.importlib.metadata, "version", lambda _: "fixture")
    metadata = bundle.source_metadata(tmp_path)
    assert len(metadata["commit"]) == 40
    assert metadata["dirty"] is False
    (tmp_path / "file.py").write_text("x = 2\n")
    with pytest.raises(bundle.BundleError, match="Commit source changes"):
        bundle.source_metadata(tmp_path)
    assert bundle.source_metadata(tmp_path, allow_dirty=True)["dirty"] is True
