"""Maintenance tests operate only on temporary local data and generated service files."""

import argparse
import json
import os
import plistlib
import sqlite3
import stat
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from arena.maintenance import MaintenanceError, backup, generate_service, restore, run_service
from arena.store import Store, digest, uid


@pytest.fixture
def stored(tmp_path):
    store = Store(tmp_path / "live")
    project_id, artifact_id, task_id = uid(), uid(), uid()
    content = b"blender-and-code-deliverable\x00"
    (store.root / "artifacts" / artifact_id).write_bytes(content)
    with store.tx():
        store.db.execute(
            "INSERT INTO projects(id,name,brief,repo,base_ref,created) VALUES(?,?,?,?,?,?)",
            (project_id, "Competition", "Latest WAL content", "", "main", time.time()),
        )
        task = store.create_task(project_id, "Work", "Implement")
        task_id = task["id"]
        store.db.execute(
            'UPDATE tasks SET status="running",lease_hash=?,lease_until=? WHERE id=?',
            (digest("old-run-token"), time.time() + 1000, task_id),
        )
        store.db.execute(
            "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)",
            (artifact_id, project_id, task_id, "model.blend", len(content), 1, time.time()),
        )
        owner = store.one('SELECT id FROM identities WHERE kind="member"')
        store.db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest("old-session"), owner["id"], time.time() + 1000))
        store.db.execute(
            "INSERT INTO machines(id,name,os,seen,slots,storage_bytes) VALUES(?,?,?,?,?,?)",
            (uid(), "Mac mini", "macOS", time.time(), 2, 0),
        )
    yield {
        "store": store,
        "root": store.root,
        "artifact": artifact_id,
        "content": content,
        "task": task_id,
        "project": project_id,
        "tmp": tmp_path,
    }
    store.db.close()


def make_backup(stored):
    destination = stored["tmp"] / "backup.zip"
    result = backup(stored["root"], destination)
    return destination, result


def rewrite_archive(source, target, replacements=None, extra=None):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as output:
        for info in original.infolist():
            output.writestr(info, (replacements or {}).get(info.filename, original.read(info.filename)))
        for info, value in extra or []:
            output.writestr(info, value)


def test_backup_uses_sqlite_snapshot_including_live_wal_and_only_recorded_artifacts(stored):
    (stored["root"] / "untracked-secret.txt").write_text("not in backup")
    destination, result = make_backup(stored)
    assert result["contains_credentials"] and result["encrypted"] is False
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            "arena.db",
            "owner-token.txt",
            f'artifacts/{stored["artifact"]}',
            "manifest.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format_version"] == 1
        assert archive.read(f'artifacts/{stored["artifact"]}') == stored["content"]
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    target = stored["tmp"] / "restored"
    outcome = restore(destination, target)
    assert outcome["interrupted_tasks"] == 1
    restored = Store(target)
    try:
        assert restored.project(stored["project"])["brief"] == "Latest WAL content"
        assert restored.task(stored["task"])["status"] == "interrupted"
        assert restored.task(stored["task"])["lease_hash"] is None
        assert restored.one("SELECT count(*) AS n FROM sessions")["n"] == 0
        assert restored.one("SELECT seen FROM machines")["seen"] == 0
        token = (target / "owner-token.txt").read_text()
        assert restored.authenticate(token)["kind"] == "member"
        assert (target / "artifacts" / stored["artifact"]).read_bytes() == stored["content"]
    finally:
        restored.db.close()
    # Backup and restore must not interrupt the still-live original task.
    assert stored["store"].task(stored["task"])["status"] == "running"


def test_backup_never_overwrites_archive(stored):
    destination, _ = make_backup(stored)
    original = destination.read_bytes()
    with pytest.raises(MaintenanceError, match="已经存在"):
        backup(stored["root"], destination)
    assert destination.read_bytes() == original


def test_backup_rejects_missing_or_mutated_artifact_and_removes_partial_output(stored):
    (stored["root"] / "artifacts" / stored["artifact"]).write_bytes(b"changed")
    destination = stored["tmp"] / "failed.zip"
    with pytest.raises(MaintenanceError, match="大小"):
        backup(stored["root"], destination)
    assert not destination.exists()


def test_backup_quota_failure_does_not_leave_archive(stored):
    destination = stored["tmp"] / "limited.zip"
    with pytest.raises(MaintenanceError, match="容量"):
        backup(stored["root"], destination, max_bytes=100)
    assert not destination.exists()


def test_backup_must_be_outside_live_data(stored):
    with pytest.raises(MaintenanceError, match="以外"):
        backup(stored["root"], stored["root"] / "backup.zip")


def test_restore_does_not_overwrite_existing_data(stored):
    source, _ = make_backup(stored)
    with pytest.raises(MaintenanceError, match="新目录或空目录"):
        restore(source, stored["root"])
    assert stored["store"].task(stored["task"])["status"] == "running"


def test_restore_accepts_empty_target_after_full_validation(stored):
    source, _ = make_backup(stored)
    target = stored["tmp"] / "empty"
    target.mkdir()
    restore(source, target)
    assert (target / "arena.db").exists()
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o700
        assert stat.S_IMODE((target / "owner-token.txt").stat().st_mode) == 0o600


def test_checksum_failure_keeps_empty_target_unchanged_and_cleans_staging(stored):
    source, _ = make_backup(stored)
    tampered = stored["tmp"] / "tampered.zip"
    content = b"x" * len(stored["content"])
    rewrite_archive(source, tampered, {f'artifacts/{stored["artifact"]}': content})
    target = stored["tmp"] / "target"
    target.mkdir()
    with pytest.raises(MaintenanceError, match="校验和"):
        restore(tampered, target)
    assert list(target.iterdir()) == []
    assert not list(target.parent.glob(".pal-arena-restore-*"))


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "artifacts/../../escape", "C:\\secret", "artifacts/evil.txt"]
)
def test_restore_rejects_path_traversal_and_unlisted_paths_before_mutation(stored, name):
    source, _ = make_backup(stored)
    altered = stored["tmp"] / "altered.zip"
    rewrite_archive(source, altered, extra=[(name, "bad")])
    target = stored["tmp"] / "target"
    with pytest.raises(MaintenanceError, match="路径"):
        restore(altered, target)
    assert not target.exists()


def test_restore_rejects_symlink_entries(stored):
    source, _ = make_backup(stored)
    altered = stored["tmp"] / "symlink.zip"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(altered, "w") as output:
        for info in original.infolist():
            if info.filename == "owner-token.txt":
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(info, original.read(info.filename))
    with pytest.raises(MaintenanceError, match="链接"):
        restore(altered, stored["tmp"] / "target")


def test_restore_rejects_duplicate_entries(stored):
    source, _ = make_backup(stored)
    altered = stored["tmp"] / "duplicate.zip"
    with pytest.warns(UserWarning):
        rewrite_archive(source, altered, extra=[("arena.db", b"bad")])
    with pytest.raises(MaintenanceError, match="重复"):
        restore(altered, stored["tmp"] / "target")


def test_restore_rejects_large_expansion(stored):
    source, _ = make_backup(stored)
    with pytest.raises(MaintenanceError, match="容量"):
        restore(source, stored["tmp"] / "target", max_bytes=100)


def test_restore_rejects_unsupported_manifest_version(stored):
    source, _ = make_backup(stored)
    altered = stored["tmp"] / "version.zip"
    with zipfile.ZipFile(source) as original:
        manifest = json.loads(original.read("manifest.json"))
    manifest["format_version"] = 99
    rewrite_archive(source, altered, {"manifest.json": json.dumps(manifest)})
    with pytest.raises(MaintenanceError, match="版本"):
        restore(altered, stored["tmp"] / "target")


def test_macos_service_generation_preserves_argument_boundaries_and_no_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_MACHINE_TOKEN", "never-embed-secret")
    result = generate_service(
        tmp_path / "services",
        "/Applications/PAL Python/bin/python",
        platform="macos",
        role="worker",
        worker_config="/Users/me/My Arena/worker.json",
        token_file="/Users/me/My Arena/token.txt",
        service_log="/Users/me/My Arena/worker.log",
    )
    service = Path(result["service_file"])
    values = plistlib.loads(service.read_bytes())
    assert values["ProgramArguments"][:4] == [
        "/Applications/PAL Python/bin/python",
        "-m",
        "arena.maintenance",
        "run-service",
    ]
    assert "/Users/me/My Arena/worker.json" in values["ProgramArguments"]
    assert "/Users/me/My Arena/token.txt" in values["ProgramArguments"]
    assert "never-embed-secret" not in service.read_text()
    assert result["installed"] is False
    commands = Path(result["commands_file"]).read_text()
    assert "launchctl bootstrap" in commands and "launchctl bootout" in commands


def test_packaged_macos_service_runs_included_executable_without_python_module(tmp_path):
    result = generate_service(
        tmp_path / "services",
        "/Applications/PAL Arena/pal-arena",
        platform="macos",
        data_dir="/Users/me/Arena Data",
        service_log="/Users/me/Arena Data/server.log",
        packaged=True,
    )
    values = plistlib.loads(Path(result["service_file"]).read_bytes())
    assert values["ProgramArguments"][:3] == ["/Applications/PAL Arena/pal-arena", "maintenance", "run-service"]


def test_windows_user_task_uses_interactive_account_and_quoted_paths(tmp_path):
    result = generate_service(
        tmp_path / "services",
        r"C:\PAL Arena\pal-arena.exe",
        platform="windows",
        role="worker",
        worker_config=r"C:\Users\Me\Arena Files\worker.json",
        token_file=r"C:\Users\Me\Arena Files\token.txt",
        user_id=r"PC\Me",
        service_log=r"C:\Users\Me\Arena Files\worker.log",
        packaged=True,
    )
    tree = ElementTree.parse(result["service_file"])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert tree.findtext(".//t:Command", namespaces=ns) == r"C:\PAL Arena\pal-arena.exe"
    assert '"C:\\Users\\Me\\Arena Files\\worker.json"' in tree.findtext(".//t:Arguments", namespaces=ns)
    assert tree.findtext(".//t:LogonType", namespaces=ns) == "InteractiveToken"
    assert tree.findtext(".//t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert result["installed"] is False


def test_service_generation_never_overwrites_existing_definition(tmp_path):
    generate_service(
        tmp_path, "/usr/bin/python3", platform="macos", data_dir="/tmp/arena", service_log="/tmp/server.log"
    )
    with pytest.raises(MaintenanceError, match="已存在"):
        generate_service(
            tmp_path, "/usr/bin/python3", platform="macos", data_dir="/tmp/arena", service_log="/tmp/server.log"
        )


def test_worker_service_reads_local_restricted_token_without_printing_it(tmp_path, monkeypatch):
    from arena import worker

    token_file = tmp_path / "machine-token.txt"
    token_file.write_text("x" * 43)
    token_file.chmod(0o600)
    observed = {}

    class FakeWorker:
        def __init__(self, config, token):
            observed["token"] = token
            observed["config"] = config

        def run(self):
            print("worker started")

    monkeypatch.setattr(worker, "Worker", FakeWorker)
    monkeypatch.setattr(worker, "load_config", lambda path: {"source": path})
    args = argparse.Namespace(
        role="worker",
        config=str(tmp_path / "worker.json"),
        token_file=str(token_file),
        log_file=str(tmp_path / "logs" / "worker.log"),
    )
    run_service(args)
    assert observed["token"] == "x" * 43
    logs = Path(args.log_file).read_text()
    assert "worker started" in logs
    assert "x" * 43 not in logs


@pytest.mark.skipif(os.name == "nt", reason="Unix mode bits differ from Windows ACLs")
def test_worker_service_rejects_world_readable_token(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("x" * 43)
    token_file.chmod(0o644)
    args = argparse.Namespace(
        role="worker", config="ignored", token_file=str(token_file), log_file=str(tmp_path / "log")
    )
    with pytest.raises(MaintenanceError, match="chmod 600"):
        run_service(args)


def test_restore_pauses_snapshot_queued_tasks_that_may_have_run_after_backup(stored):
    with stored["store"].tx():
        queued = stored["store"].create_task(stored["project"], "Queued deployment", "Publish", kind="publisher")
    source, _ = make_backup(stored)
    target = stored["tmp"] / "restored"
    result = restore(source, target)
    assert result["interrupted_tasks"] == 2
    db = sqlite3.connect(target / "arena.db")
    try:
        assert db.execute("SELECT status FROM tasks WHERE id=?", (queued["id"],)).fetchone()[0] == "interrupted"
    finally:
        db.close()


@pytest.mark.parametrize("has_agent_hash", [False, True])
def test_restore_invalidates_agent_action_tokens_and_supports_older_schemas(stored, has_agent_hash):
    with stored["store"].tx():
        if has_agent_hash:
            stored["store"].db.execute(
                "UPDATE tasks SET agent_hash=? WHERE id=?", (digest("old-agent-token"), stored["task"])
            )
        else:
            stored["store"].db.execute("ALTER TABLE tasks DROP COLUMN agent_hash")
    source, _ = make_backup(stored)
    target = stored["tmp"] / "restored"
    restore(source, target)
    db = sqlite3.connect(target / "arena.db")
    try:
        assert db.execute("SELECT lease_hash,lease_until FROM tasks").fetchone() == (None, None)
        if has_agent_hash:
            assert db.execute("SELECT agent_hash FROM tasks").fetchone() == (None,)
        else:
            assert "agent_hash" not in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
    finally:
        db.close()


def test_artifact_cleanup_during_compression_cannot_break_snapshot(stored, monkeypatch):
    from arena import maintenance

    original = maintenance.file_hash
    cleaned = False

    def file_hash(path):
        nonlocal cleaned
        if not cleaned:
            cleaned = True
            with stored["store"].tx():
                (stored["root"] / "artifacts" / stored["artifact"]).unlink()
                stored["store"].db.execute("DELETE FROM artifacts WHERE id=?", (stored["artifact"],))
        return original(path)

    monkeypatch.setattr(maintenance, "file_hash", file_hash)
    archive, _ = make_backup(stored)
    target = stored["tmp"] / "restored"
    restore(archive, target)
    assert (target / "artifacts" / stored["artifact"]).read_bytes() == stored["content"]
    assert stored["store"].one("SELECT count(*) AS n FROM artifacts")["n"] == 0
