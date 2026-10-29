"""Cleanup uses temporary Git repositories and mocked remotes, never real agents or GitHub."""

import copy
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from arena import workspaces


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def completed_workspaces(tmp_path, monkeypatch):
    global_config = tmp_path / "git-config"
    global_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "workspaces"
    root.mkdir()
    statuses = {"tasks": []}
    records = []
    for index in range(4):
        task_id = f"task-{index}"
        workspace = root / "project" / task_id / "1"
        workspace.mkdir(parents=True)
        git(workspace, "init")
        git(workspace, "config", "user.name", "Fixture")
        git(workspace, "config", "user.email", "fixture@example.invalid")
        (workspace / "source.py").write_text(f"print({index})\n", encoding="utf-8")
        git(workspace, "add", "source.py")
        git(workspace, "commit", "-m", "Fixture source")
        git(workspace, "remote", "add", "origin", "https://github.com/example/competition.git")
        commit = git(workspace, "rev-parse", "HEAD")
        evidence = {
            "machine_id": "fixture-machine",
            "workspace": str(workspace),
            "commit": commit,
            "branch": f"arena/{task_id}/1",
            "delivery": "github",
        }
        receipt = {
            "task_id": task_id,
            "project_id": "project",
            "attempt": 1,
            "requirement_revision": 1,
            "updated": index + 1,
            "status": "complete_acknowledged",
            "result": "Completed",
            "evidence": copy.deepcopy(evidence),
        }
        path = root / "_receipts" / task_id / "1.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(receipt), encoding="utf-8")
        statuses["tasks"].append(
            {
                "id": task_id,
                "attempt": 1,
                "project_id": "project",
                "status": "done",
                "kind": "worker",
                "revision": 1,
                "evidence": evidence,
            }
        )
        records.append({"workspace": workspace, "receipt_path": path, "receipt": receipt})
    return root, statuses, records


def preview(fixture, verifier=None):
    root, statuses, _ = fixture
    return workspaces.plan(root, statuses, verifier or (lambda *_: True))


def save_record(record):
    record["receipt_path"].write_text(json.dumps(record["receipt"]), encoding="utf-8")


def test_plan_is_read_only_and_keeps_three_latest_copies_per_project(completed_workspaces):
    root, _, records = completed_workspaces
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    verifier = Mock(return_value=True)
    result = preview(completed_workspaces, verifier)
    assert [item["task_id"] for item in result["candidates"]] == ["task-0"]
    assert {item["task_id"] for item in result["preserved"]} == {"task-1", "task-2", "task-3"}
    assert result["reclaimable_bytes"] > 0
    verifier.assert_called_once_with(
        "https://github.com/example/competition.git", "arena/task-0/1", records[0]["receipt"]["evidence"]["commit"]
    )
    after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after


def test_git_toplevel_is_compared_as_a_normalized_path(completed_workspaces, monkeypatch):
    original = workspaces._git

    def normalized_equivalent(path, *args):
        result = original(path, *args)
        return result + "/." if args == ("rev-parse", "--show-toplevel") else result

    monkeypatch.setattr(workspaces, "_git", normalized_equivalent)
    assert len(preview(completed_workspaces)["candidates"]) == 1


@pytest.mark.parametrize(
    "state", ["running", "queued", "in_review", "coordinating", "failed", "interrupted", "cancelled"]
)
def test_nonaccepted_server_state_preserves_workspace(completed_workspaces, state):
    completed_workspaces[1]["tasks"][0]["status"] = state
    assert preview(completed_workspaces)["candidates"] == []


def test_preview_explains_that_active_tasks_are_preserved(completed_workspaces):
    completed_workspaces[1]["tasks"][0]["status"] = "running"
    result = preview(completed_workspaces)
    preserved = next(item for item in result["preserved"] if item["task_id"] == "task-0")
    assert "仍在排队、执行或验收中" in preserved["reason"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_uncertain", True),
        ("checkpoint", "failed_run"),
        ("push_status", "unconfirmed"),
        ("delivery", "local"),
        ("merge_status", "outcome_unknown"),
        ("deployment_status", "outcome_unknown"),
    ],
)
def test_uncertain_failed_or_local_only_receipts_are_preserved(completed_workspaces, field, value):
    record = completed_workspaces[2][0]
    record["receipt"]["evidence"][field] = value
    save_record(record)
    assert preview(completed_workspaces)["candidates"] == []


@pytest.mark.parametrize("mutation", ["missing", "attempt", "revision", "commit", "workspace", "machine"])
def test_server_status_must_match_the_exact_recorded_output(completed_workspaces, mutation):
    status = completed_workspaces[1]["tasks"][0]
    if mutation == "missing":
        completed_workspaces[1]["tasks"].pop(0)
    elif mutation in ("attempt", "revision"):
        status[mutation] = 2
    else:
        status["evidence"][{"machine": "machine_id"}.get(mutation, mutation)] = "different"
    assert preview(completed_workspaces)["candidates"] == []


@pytest.mark.parametrize("mutation", ["missing", "invalid", "unconfirmed", "mismatched"])
def test_missing_or_unconfirmed_receipt_is_never_cleaned(completed_workspaces, mutation):
    record = completed_workspaces[2][0]
    if mutation == "missing":
        record["receipt_path"].unlink()
    elif mutation == "invalid":
        record["receipt_path"].write_text("INVALID RECEIPT SECRET", encoding="utf-8")
    else:
        record["receipt"]["status" if mutation == "unconfirmed" else "task_id"] = "different"
        save_record(record)
    result = preview(completed_workspaces)
    assert "task-0" not in {item["task_id"] for item in result["candidates"]}
    assert any(item["task_id"] == "task-0" for item in result["preserved"])
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("content", ["tracked", "untracked", "ignored", "exports", "changed_commit"])
def test_local_changes_and_exports_are_preserved(completed_workspaces, content):
    workspace = completed_workspaces[2][0]["workspace"]
    if content == "tracked":
        (workspace / "source.py").write_text("new local work", encoding="utf-8")
    elif content == "untracked":
        (workspace / "new-model.blend").write_bytes(b"local output")
    elif content in ("ignored", "exports"):
        name = "arena-exports" if content == "exports" else "private-data"
        (workspace / name).mkdir()
        (workspace / name / "result.blend").write_bytes(b"output")
        (workspace / ".git" / "info" / "exclude").write_text(name + "/\n", encoding="utf-8")
    else:
        git(workspace, "commit", "--allow-empty", "-m", "Unpublished change")
    assert preview(completed_workspaces)["candidates"] == []


def test_downloaded_artifact_cache_does_not_prevent_cleanup(completed_workspaces):
    workspace = completed_workspaces[2][0]["workspace"]
    (workspace / ".arena-inputs" / "artifact-id").mkdir(parents=True)
    (workspace / ".arena-inputs" / "artifact-id" / "input.blend").write_bytes(b"server artifact copy")
    proof = [{"id": "artifact-id", "sha256": hashlib.sha256(b"server artifact copy").hexdigest()}]
    record = completed_workspaces[2][0]
    record["receipt"]["evidence"]["input_artifacts"] = proof
    save_record(record)
    completed_workspaces[1]["tasks"][0]["evidence"]["input_artifacts"] = proof
    assert len(preview(completed_workspaces)["candidates"]) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_unrecorded_or_modified_input_artifacts_are_preserved(completed_workspaces, changed):
    record = completed_workspaces[2][0]
    folder = record["workspace"] / ".arena-inputs" / "artifact-id"
    folder.mkdir(parents=True)
    (folder / "model.blend").write_bytes(b"unique local edits")
    if changed:
        proof = [{"id": "artifact-id", "sha256": hashlib.sha256(b"original downloaded artifact").hexdigest()}]
        record["receipt"]["evidence"]["input_artifacts"] = proof
        save_record(record)
        completed_workspaces[1]["tasks"][0]["evidence"]["input_artifacts"] = proof
    assert preview(completed_workspaces)["candidates"] == []


@pytest.mark.parametrize("hidden", ["stash", "branch", "reflog", "assume-unchanged", "skip-worktree"])
def test_git_hidden_or_unpublished_work_is_preserved(completed_workspaces, hidden):
    workspace = completed_workspaces[2][0]["workspace"]
    recorded = git(workspace, "rev-parse", "HEAD")
    if hidden in ("assume-unchanged", "skip-worktree"):
        git(workspace, "update-index", "--" + hidden, "source.py")
        (workspace / "source.py").write_text("unique hidden edit", encoding="utf-8")
    else:
        if hidden == "branch":
            git(workspace, "checkout", "-b", "unpublished-work")
        (workspace / "source.py").write_text("unique hidden edit", encoding="utf-8")
        if hidden == "stash":
            git(workspace, "stash", "push")
        else:
            git(workspace, "commit", "-am", "Unpublished work")
            if hidden == "branch":
                git(workspace, "checkout", "--detach", recorded)
            else:
                git(workspace, "reset", "--hard", recorded)
    assert preview(completed_workspaces)["candidates"] == []


@pytest.mark.parametrize(
    "remote",
    [
        "https://user:PRIVATE_TOKEN@github.com/example/repo",
        "https://example.com/repo",
        "/tmp/local-repo",
        "git@github.com:example/repo.git",
    ],
)
def test_unsafe_or_unverified_remote_is_preserved_without_leaking_values(completed_workspaces, remote):
    workspace = completed_workspaces[2][0]["workspace"]
    git(workspace, "remote", "set-url", "origin", remote)
    verifier = Mock(return_value=True)
    result = preview(completed_workspaces, verifier)
    assert result["candidates"] == []
    assert "PRIVATE_TOKEN" not in json.dumps(result)
    verifier.assert_not_called()


def test_remote_verification_error_is_preserved_without_exception_details(completed_workspaces):
    result = preview(completed_workspaces, Mock(side_effect=RuntimeError("credential REMOTE_SECRET")))
    assert result["candidates"] == []
    assert "REMOTE_SECRET" not in json.dumps(result)


def test_symlinked_workspace_and_external_directories_are_never_removed(completed_workspaces, tmp_path):
    root, statuses, records = completed_workspaces
    source = records[0]["workspace"]
    outside = tmp_path / "outside"
    source.rename(outside)
    try:
        source.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("This Windows account cannot create directory symlinks")
        raise
    (root / "unmanaged").mkdir()
    (root / "unmanaged" / "unique.txt").write_text("keep", encoding="utf-8")
    result = workspaces.plan(root, statuses, lambda *_: True)
    assert result["candidates"] == []
    assert (outside / "source.py").exists()
    assert (root / "unmanaged" / "unique.txt").read_text() == "keep"


def test_receipt_cannot_redirect_deletion_outside_managed_layout(completed_workspaces, tmp_path):
    record = completed_workspaces[2][0]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "valuable.blend").write_bytes(b"keep")
    record["receipt"]["evidence"]["workspace"] = str(outside)
    save_record(record)
    completed_workspaces[1]["tasks"][0]["evidence"]["workspace"] = str(outside)
    assert preview(completed_workspaces)["candidates"] == []
    assert (outside / "valuable.blend").exists()


def test_apply_rechecks_and_only_removes_previewed_candidates_preserving_receipts(completed_workspaces):
    root, statuses, records = completed_workspaces
    proposed = preview(completed_workspaces)
    result = workspaces.apply_plan(root, proposed, lambda _: statuses, lambda *_: True)
    assert len(result["removed"]) == 1
    assert not records[0]["workspace"].exists()
    assert all(record["workspace"].exists() for record in records[1:])
    receipt = json.loads(records[0]["receipt_path"].read_text())
    assert receipt["status"] == "complete_acknowledged"
    assert receipt["workspace_cleanup"]["status"] == "removed"
    assert receipt["workspace_cleanup"]["completed_at"] >= receipt["workspace_cleanup"]["started_at"]


def test_apply_plan_locked_reuses_worker_held_workspace_lock(completed_workspaces):
    root, statuses, records = completed_workspaces
    proposed = preview(completed_workspaces)
    with workspaces.workspace_lock(root):
        result = workspaces.apply_plan_locked(root, proposed, lambda _items: statuses, remote_verifier=lambda *_: True)
    assert [item["task_id"] for item in result["removed"]] == ["task-0"]
    assert not records[0]["workspace"].exists()
    receipt = json.loads(records[0]["receipt_path"].read_text(encoding="utf-8"))
    assert receipt["workspace_cleanup"]["status"] == "removed"
    if os.name != "nt":
        assert stat.S_IMODE(records[0]["receipt_path"].stat().st_mode) == 0o600


@pytest.mark.parametrize("change", ["server", "local", "remote"])
def test_apply_retains_candidate_that_changed_after_preview(completed_workspaces, change):
    root, statuses, records = completed_workspaces
    proposed = preview(completed_workspaces)
    if change == "server":
        statuses["tasks"][0]["status"] = "running"
    elif change == "local":
        (records[0]["workspace"] / "new-work.txt").write_text("keep", encoding="utf-8")
    result = workspaces.apply_plan(root, proposed, lambda _: statuses, lambda *_: change != "remote")
    assert result["removed"] == []
    assert records[0]["workspace"].exists()


def test_apply_rechecks_local_files_after_final_remote_verification(completed_workspaces):
    root, statuses, records = completed_workspaces
    proposed = preview(completed_workspaces)
    calls = 0

    def slow_remote(*_):
        nonlocal calls
        calls += 1
        if calls == 2:
            (records[0]["workspace"] / "source.py").write_text("edit during remote check", encoding="utf-8")
        return True

    result = workspaces.apply_plan(root, proposed, lambda _: statuses, slow_remote)
    assert result["removed"] == []
    assert records[0]["workspace"].exists()


def test_worker_process_lock_prevents_cleanup_before_status_or_delete(completed_workspaces):
    root, statuses, records = completed_workspaces
    proposed = preview(completed_workspaces)
    provider = Mock(return_value=statuses)
    with workspaces.workspace_lock(root), pytest.raises(workspaces.WorkspaceBusy):
        workspaces.apply_plan(root, proposed, provider, lambda *_: True)
    provider.assert_not_called()
    assert records[0]["workspace"].exists()
    # A released lock remains reusable even though the lock file persists.
    with workspaces.workspace_lock(root):
        pass


def test_release_workspace_uses_confirmed_published_ref(completed_workspaces):
    root, statuses, records = completed_workspaces
    original = records[0]
    destination = root / "releases" / "project" / "task-0" / "1"
    destination.parent.mkdir(parents=True)
    original["workspace"].rename(destination)
    original["receipt"]["evidence"].update(workspace=str(destination), published_ref="main", merge_status="completed")
    save_record(original)
    statuses["tasks"][0].update(kind="publisher", evidence=copy.deepcopy(original["receipt"]["evidence"]))
    verifier = Mock(return_value=True)
    result = preview(completed_workspaces, verifier)
    assert result["candidates"][0]["workspace"] == str(destination)
    assert result["candidates"][0]["branch"] == "main"


def test_remote_ancestor_check_runs_only_in_temporary_repository(monkeypatch, tmp_path):
    commit, tip = "a" * 40, "b" * 40
    calls = []

    def fake_git(path, *args):
        calls.append((Path(path), args))
        if args[:2] == ("remote", "get-url"):
            return "https://github.com/example/project"
        if args[0] == "ls-remote":
            return tip + "\trefs/heads/arena/task/1"
        if args[0] == "rev-parse":
            return tip
        if args[0] == "cat-file":
            return "commit"
        return ""

    monkeypatch.setattr(workspaces, "_git", fake_git)
    assert workspaces.verify_remote("https://github.com/example/project", "arena/task/1", commit)
    assert any(args == ("merge-base", "--is-ancestor", commit, tip) for _, args in calls)
    assert len({path for path, _ in calls}) == 1
    assert all(not path.exists() for path, _ in calls)


@pytest.mark.parametrize("remote_state", ["descendant", "rewritten", "missing"])
def test_real_git_verifies_commit_reachability_in_a_local_bare_remote(
    completed_workspaces, tmp_path, monkeypatch, remote_state
):
    source = completed_workspaces[2][0]["workspace"]
    recorded = git(source, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    branch = "arena/task/1"
    if remote_state == "rewritten":
        git(source, "checkout", "--orphan", "unrelated-history")
    git(source, "commit", "--allow-empty", "-m", "New remote commit")
    if remote_state != "missing":
        git(source, "push", str(remote), "HEAD:refs/heads/" + branch)

    def local_transport(path, *args):
        # This test-only adapter allows the fixture's file transport; production accepts HTTPS GitHub only.
        try:
            return git(path, *args)
        except subprocess.CalledProcessError:
            raise workspaces.CleanupError("Fixture Git verification failed") from None

    monkeypatch.setattr(workspaces, "_git", local_transport)
    monkeypatch.setattr(workspaces, "GITHUB", re.compile(re.escape(str(remote)) + r"\Z"))
    assert workspaces.verify_remote(str(remote), branch, recorded) is (remote_state == "descendant")


def test_cleanup_reads_restricted_token_without_exposing_it(completed_workspaces, tmp_path, monkeypatch):
    root, statuses, _ = completed_workspaces
    config = tmp_path / "worker.json"
    config.write_text(
        json.dumps(
            {
                "server": "http://127.0.0.1:8765",
                "workspace_root": str(root),
                "agents": [{"key": "worker", "name": "Worker", "provider": "codex"}],
            }
        ),
        encoding="utf-8",
    )
    token = tmp_path / "machine-token"
    token.write_text("PRIVATE_MACHINE_CREDENTIAL_12345", encoding="utf-8")
    token.chmod(0o600)
    client = Mock()
    client.request.return_value = statuses
    factory = Mock(return_value=client)
    monkeypatch.setattr("arena.worker.API", factory)
    monkeypatch.setattr(workspaces, "verify_remote", lambda *_: True)
    result = workspaces.cleanup(config, token)
    assert len(result["candidates"]) == 1
    assert "PRIVATE_MACHINE_CREDENTIAL" not in json.dumps(result)
    assert factory.call_args.args[1] == "PRIVATE_MACHINE_CREDENTIAL_12345"
    assert client.request.call_args.args[1] == "/api/machines/workspaces/status"
    client.close.assert_called_once()
    if os.name != "nt":
        token.chmod(0o644)
        with pytest.raises(workspaces.CleanupError):
            workspaces.cleanup(config, token)
