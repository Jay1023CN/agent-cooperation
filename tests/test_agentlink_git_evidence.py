import subprocess

import pytest

from agent_link.desktop_handoff import build_server
from agent_link.git_evidence import EvidenceError, inspect_git_delivery
from agent_link.handoff_store import HandoffStore


def git(path, *args):
    return (
        subprocess.check_output(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
            cwd=path,
            stderr=subprocess.DEVNULL,
        )
        .decode()
        .strip()
    )


def test_verify_git_delivery_checks_actual_files_and_dirty_state(tmp_path):
    git(tmp_path, "init")
    (tmp_path / "file.txt").write_text("first", encoding="utf-8")
    git(tmp_path, "add", "file.txt")
    git(tmp_path, "commit", "-m", "first")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "file.txt").write_text("second", encoding="utf-8")
    git(tmp_path, "add", "file.txt")
    git(tmp_path, "commit", "-m", "second")
    head = git(tmp_path, "rev-parse", "HEAD")
    result = inspect_git_delivery(str(tmp_path), head, base)
    assert result["clean"] and result["base_is_ancestor"]
    assert result["author"] == {"name": "Test", "email": "test@example.invalid"}
    assert result["committer"] == {"name": "Test", "email": "test@example.invalid"}
    assert "not proof of a GitHub account" in result["note"]
    assert result["changed_paths"] == ["file.txt"]
    (tmp_path / "untracked.txt").write_text("pending", encoding="utf-8")
    assert not inspect_git_delivery(str(tmp_path), head, base)["clean"]
    with pytest.raises(EvidenceError, match="HEAD"):
        inspect_git_delivery(str(tmp_path), base, base)
    with pytest.raises(EvidenceError, match="Full commit"):
        inspect_git_delivery(str(tmp_path), "HEAD", base)
    nested = tmp_path / "nested"
    nested.mkdir()
    with pytest.raises(EvidenceError, match="worktree root"):
        inspect_git_delivery(str(nested), head, base)


@pytest.mark.asyncio
async def test_git_evidence_is_available_only_after_candidate_submission(tmp_path):
    git(tmp_path, "init")
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    git(tmp_path, "add", "file.txt")
    git(tmp_path, "commit", "-m", "first")
    commit = git(tmp_path, "rev-parse", "HEAD")
    db = tmp_path.parent / (tmp_path.name + ".db")
    store = HandoffStore(db)
    store.create("root", "task", {"workspace": str(tmp_path), "prompt": "work"})
    host = build_server(store, "host")
    args = {"lineage": "root", "expected_commit": commit, "base_commit": commit}
    with pytest.raises(Exception, match="submitted candidate"):
        await host.call_tool("verify_git_delivery", args)
    claimed = store.claim("root")
    store.submit("root", claimed["handle"], {"summary": "done", "evidence": ["commit"]})
    response = await host.call_tool("verify_git_delivery", args)
    assert response


def test_git_evidence_rejects_workspace_change_during_inspection(tmp_path, monkeypatch):
    git(tmp_path, "init")
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    git(tmp_path, "add", "file.txt")
    git(tmp_path, "commit", "-m", "first")
    commit = git(tmp_path, "rev-parse", "HEAD")
    original = subprocess.run

    def run(*args, **kwargs):
        result = original(*args, **kwargs)
        if "diff" in args[0]:
            (tmp_path / "file.txt").write_text("changed during inspection", encoding="utf-8")
        return result

    monkeypatch.setattr("agent_link.git_evidence.subprocess.run", run)
    with pytest.raises(EvidenceError, match="changed while"):
        inspect_git_delivery(str(tmp_path), commit, commit)


def test_git_checks_do_not_inherit_task_credentials_or_mcp_input(tmp_path, monkeypatch):
    git(tmp_path, "init")
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    git(tmp_path, "add", "file.txt")
    git(tmp_path, "commit", "-m", "first")
    commit = git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.setenv("MULTICA_TOKEN", "private")
    monkeypatch.setenv("COOPERATION_COORDINATION_TOKEN", "private")
    original = subprocess.run
    observed = []

    def run(*args, **kwargs):
        observed.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr("agent_link.git_evidence.subprocess.run", run)
    inspect_git_delivery(str(tmp_path), commit, commit)
    assert observed
    assert all(call["stdin"] == subprocess.DEVNULL for call in observed)
    assert all(
        "MULTICA_TOKEN" not in call["env"] and "COOPERATION_COORDINATION_TOKEN" not in call["env"] for call in observed
    )
