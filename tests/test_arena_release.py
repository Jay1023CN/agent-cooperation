"""Release integration tests use local temporary Git repositories, never remote writes."""

import copy
import os
import subprocess
import sys
import threading
import time

import pytest

from arena import release


def git(cwd, *args):
    return subprocess.check_output(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=cwd,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    (source / "README.md").write_text("base\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "base")
    base = git(source, "rev-parse", "HEAD")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "-u", "origin", "main")
    commits = []
    for index in range(2):
        git(source, "checkout", "-b", f"worker-{index}", "main")
        (source / f"part-{index}.txt").write_text(f"reviewed-{index}\n")
        git(source, "add", ".")
        git(source, "commit", "-m", f"worker {index}")
        commits.append(git(source, "rev-parse", "HEAD"))
        git(source, "push", "origin", f"worker-{index}")
    git(source, "checkout", "main")
    check = release.checked_repo

    def local_repo(value):
        assert check(value) == "https://github.com/team/project.git"
        return str(remote)

    monkeypatch.setattr(release, "checked_repo", local_repo)
    job = {
        "project": {
            "id": "project",
            "repo": "https://github.com/team/project.git",
            "base_ref": "main",
            "revision": 2,
            "policy": {"auto_merge": True, "auto_deploy": False, "release_profile": "local"},
        },
        "task": {"id": "release-task", "attempt": 1, "channel": "shared"},
        "dependencies": [
            {
                "id": f"worker-{i}",
                "kind": "worker",
                "status": "done",
                "revision": 2,
                "capability": "code",
                "review": {"decision": "accept", "evidence": ["Independently checked actual files"]},
                "evidence": {"commit": commit, "delivery": "github"},
            }
            for i, commit in enumerate(commits)
        ],
    }
    validation = [
        sys.executable,
        "-c",
        'from pathlib import Path; assert Path("part-0.txt").read_text()=="reviewed-0\\n"; '
        'assert Path("part-1.txt").read_text()=="reviewed-1\\n"',
    ]
    config = {"release_profiles": {"local": {"validation_commands": [validation]}}}
    return {
        "job": job,
        "config": config,
        "root": tmp_path / "workspaces",
        "remote": remote,
        "source": source,
        "base": base,
        "commits": commits,
        "logs": [],
    }


def execute(s, checkpoint=None):
    return release.execute_release(
        s["job"], s["config"], s["root"], s["logs"].append, threading.Event(), checkpoint=checkpoint
    )


def test_release_merges_independently_reviewed_commits_and_pushes_only_validated_tree(scenario):
    result, evidence = execute(scenario)
    assert evidence["merge_status"] == "completed"
    assert evidence["deployment_status"] == "not_requested"
    assert evidence["validation"] == [{"step": 1, "status": "passed"}]
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == evidence["commit"]
    for commit in scenario["commits"]:
        git(scenario["remote"], "merge-base", "--is-ancestor", commit, evidence["commit"])
    assert "2 份验收提交" in result
    assert git(scenario["source"], "rev-parse", "HEAD") == scenario["base"]


def test_deploy_without_merge_executes_local_profile_after_validation(scenario, tmp_path):
    marker = tmp_path / "deployed.txt"
    scenario["job"]["project"]["policy"].update(auto_merge=False, auto_deploy=True)
    scenario["config"]["release_profiles"]["local"]["deploy_command"] = [
        sys.executable,
        "-c",
        'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("deployed")',
        str(marker),
    ]
    _, evidence = execute(scenario)
    assert marker.read_text() == "deployed"
    assert evidence["deployment_status"] == "completed"
    assert evidence["merge_status"] == "not_requested"
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == scenario["base"]


def test_failed_validation_cannot_push_or_deploy(scenario, tmp_path):
    marker = tmp_path / "never-deploy"
    scenario["job"]["project"]["policy"]["auto_deploy"] = True
    profile = scenario["config"]["release_profiles"]["local"]
    profile["validation_commands"] = [[sys.executable, "-c", "raise SystemExit(7)"]]
    profile["deploy_command"] = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    with pytest.raises(release.ReleaseError) as exc:
        execute(scenario)
    assert exc.value.evidence["merge_status"] == "not_started"
    assert not marker.exists()
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == scenario["base"]


def test_validation_must_not_modify_reviewed_source(scenario):
    scenario["config"]["release_profiles"]["local"]["validation_commands"].append(
        [sys.executable, "-c", 'from pathlib import Path; Path("part-0.txt").write_text("unreviewed")']
    )
    with pytest.raises(release.ReleaseError):
        execute(scenario)
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == scenario["base"]


def test_remote_branch_advance_stops_release_instead_of_force_pushing(scenario, monkeypatch):
    original = release.run
    pushes = []

    def run(command, cwd, cancelled, timeout):
        if command[:2] == ["git", "ls-remote"]:
            return "f" * 40 + "\trefs/heads/main"
        if command[:2] == ["git", "push"]:
            pushes.append(command)
        return original(command, cwd, cancelled, timeout)

    monkeypatch.setattr(release, "run", run)
    with pytest.raises(release.ReleaseError, match="目标分支已变化"):
        execute(scenario)
    assert not pushes
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == scenario["base"]


def test_uncertain_push_is_attempted_once_and_preserves_outcome_unknown(scenario, monkeypatch):
    original = release.run
    pushes = []

    def run(command, cwd, cancelled, timeout):
        result = original(command, cwd, cancelled, timeout)
        if command[:2] == ["git", "push"]:
            pushes.append(command)
            raise release.ReleaseError("response lost after remote write")
        return result

    monkeypatch.setattr(release, "run", run)
    with pytest.raises(release.ReleaseError, match="系统不会自动重发") as exc:
        execute(scenario)
    assert len(pushes) == 1
    assert not any("--force" in part or part.startswith("+") for part in pushes[0])
    assert exc.value.evidence["merge_status"] == "outcome_unknown"
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == exc.value.evidence["commit"]


def test_failed_deployment_retains_successful_merge_and_unknown_deployment(scenario):
    scenario["job"]["project"]["policy"]["auto_deploy"] = True
    scenario["config"]["release_profiles"]["local"]["deploy_command"] = [sys.executable, "-c", "raise SystemExit(2)"]
    with pytest.raises(release.ReleaseError, match="外部结果可能已发生") as exc:
        execute(scenario)
    assert exc.value.evidence["merge_status"] == "completed"
    assert exc.value.evidence["deployment_status"] == "outcome_unknown"
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == exc.value.evidence["commit"]


def test_release_checkpoints_surround_each_external_effect(scenario, tmp_path):
    marker = tmp_path / "deployed"
    scenario["job"]["project"]["policy"]["auto_deploy"] = True
    scenario["config"]["release_profiles"]["local"]["deploy_command"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
        str(marker),
    ]
    checkpoints = []

    def checkpoint(evidence):
        checkpoints.append(copy.deepcopy(evidence))
        stage = evidence["release_stage"]
        remote = git(scenario["remote"], "rev-parse", "refs/heads/main")
        assert remote == (scenario["base"] if stage == "merge_pending" else evidence["commit"])
        assert marker.exists() == (stage == "deployment_confirmed")
        assert evidence["execution_uncertain"] == stage.endswith("_pending")

    _, evidence = execute(scenario, checkpoint)
    assert [item["release_stage"] for item in checkpoints] == [
        "merge_pending",
        "merge_confirmed",
        "deployment_pending",
        "deployment_confirmed",
    ]
    assert checkpoints[0]["merge_status"] == "outcome_unknown"
    assert checkpoints[2]["merge_status"] == "completed"
    assert checkpoints[2]["deployment_status"] == "outcome_unknown"
    assert checkpoints[-1]["deployment_status"] == "completed"
    assert evidence["execution_uncertain"] is False


@pytest.mark.parametrize("crash_stage", ["merge_pending", "deployment_pending"])
def test_hard_interruption_leaves_intent_and_exact_commit_in_last_checkpoint(
    scenario, tmp_path, monkeypatch, crash_stage
):
    class SimulatedPowerLoss(BaseException):
        pass

    marker = tmp_path / "deployed-before-power-loss"
    scenario["job"]["project"]["policy"]["auto_deploy"] = True
    deployment = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
        str(marker),
    ]
    scenario["config"]["release_profiles"]["local"]["deploy_command"] = deployment
    checkpoints, effects = [], []
    original = release.run

    def run(command, cwd, cancelled, timeout):
        stage = "merge_pending" if command[:2] == ["git", "push"] else None
        if command == deployment:
            stage = "deployment_pending"
        if stage:
            assert checkpoints[-1]["release_stage"] == stage
            assert checkpoints[-1]["execution_uncertain"] is True
            effects.append(stage)
        result = original(command, cwd, cancelled, timeout)
        if stage == crash_stage:
            raise SimulatedPowerLoss()
        return result

    monkeypatch.setattr(release, "run", run)
    with pytest.raises(SimulatedPowerLoss):
        execute(scenario, lambda evidence: checkpoints.append(copy.deepcopy(evidence)))
    assert checkpoints[-1]["release_stage"] == crash_stage
    assert checkpoints[-1]["execution_uncertain"] is True
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == checkpoints[-1]["commit"]
    assert effects.count(crash_stage) == 1
    assert marker.exists() == (crash_stage == "deployment_pending")


@pytest.mark.parametrize("fail_at", ["merge_pending", "merge_confirmed"])
def test_receipt_persistence_failure_stops_before_the_next_external_action(scenario, tmp_path, fail_at):
    marker = tmp_path / "never-deploy"
    scenario["job"]["project"]["policy"]["auto_deploy"] = True
    scenario["config"]["release_profiles"]["local"]["deploy_command"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
        str(marker),
    ]

    def checkpoint(evidence):
        if evidence["release_stage"] == fail_at:
            raise OSError("disk full")

    with pytest.raises(release.ReleaseError, match="无法保存发布检查点") as exc:
        execute(scenario, checkpoint)
    expected = scenario["base"] if fail_at == "merge_pending" else exc.value.evidence["commit"]
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == expected
    assert not marker.exists()
    assert exc.value.evidence["deployment_status"] == "not_started"


def test_same_attempt_directory_is_not_reused_for_automatic_republish(scenario):
    execute(scenario)
    with pytest.raises(release.ReleaseError, match="已有工作目录"):
        execute(scenario)


@pytest.mark.parametrize(
    "change",
    [
        {"auto_merge": False, "auto_deploy": False},
        {"release_profile": ""},
        {"release_profile": "missing"},
        {"auto_deploy": True},
    ],
)
def test_missing_release_policy_or_local_configuration_fails_before_git(scenario, change):
    scenario["job"]["project"]["policy"].update(change)
    with pytest.raises(release.ReleaseError):
        execute(scenario)
    assert not scenario["root"].exists()


@pytest.mark.parametrize("replacement", [None, [], ["pytest"], [["python", ""]], [["python", None]]])
def test_validation_commands_must_be_nonempty_argv_arrays(scenario, replacement):
    scenario["config"]["release_profiles"]["local"]["validation_commands"] = replacement
    with pytest.raises(release.ReleaseError):
        execute(scenario)
    assert not scenario["root"].exists()


@pytest.mark.parametrize(
    "change",
    [
        {"status": "in_review"},
        {"revision": 1},
        {"review": None},
        {"channel": "personal:member"},
        {"review": {"decision": "accept", "evidence": []}},
        {"review": {"decision": "changes_requested", "evidence": ["failed"]}},
        {"evidence": {"commit": "--upload-pack=bad"}},
    ],
)
def test_unreviewed_or_stale_dependencies_cannot_release(scenario, change):
    scenario["job"]["dependencies"][0].update(copy.deepcopy(change))
    with pytest.raises(release.ReleaseError):
        execute(scenario)
    assert not scenario["root"].exists()


def test_no_commits_cannot_release(scenario):
    scenario["job"]["dependencies"] = []
    with pytest.raises(release.ReleaseError, match="至少需要"):
        execute(scenario)


def test_cancelled_release_does_not_start_git(scenario):
    event = threading.Event()
    event.set()
    with pytest.raises(release.ReleaseError, match="取消"):
        release.execute_release(scenario["job"], scenario["config"], scenario["root"], lambda _: None, event)
    assert git(scenario["remote"], "rev-parse", "refs/heads/main") == scenario["base"]


def test_command_timeout_and_cancellation_terminate_process(tmp_path):
    started = time.monotonic()
    with pytest.raises(release.ReleaseError, match="超时"):
        release.run([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, threading.Event(), 0.1)
    assert time.monotonic() - started < 5
    event = threading.Event()
    timer = threading.Timer(0.1, event.set)
    timer.start()
    try:
        with pytest.raises(release.ReleaseError, match="取消"):
            release.run([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, event, 60)
    finally:
        timer.cancel()


def test_output_is_bounded_and_run_credentials_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_RUN_TOKEN", "secret-run-token")
    output = release.run(
        [sys.executable, "-c", 'import os; assert "ARENA_RUN_TOKEN" not in os.environ; print("x"*200000)'],
        tmp_path,
        threading.Event(),
        5,
    )
    assert len(output) <= release.MAX_OUTPUT
    assert "secret-run-token" not in output


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.invalid/a/b",
        "https://user:pass@github.com/a/b",
        "https://github.com/a/b?token=x",
        "https://github.com/../b",
    ],
)
def test_untrusted_repository_is_rejected(value):
    with pytest.raises(release.ReleaseError):
        release.checked_repo(value)


@pytest.mark.parametrize("value", ["--force", "../main", "refs//main", "main.lock", "main\n"])
def test_invalid_base_ref_rejected(value):
    with pytest.raises(release.ReleaseError):
        release.checked_ref(value)


def test_first_release_creates_main_from_shared_empty_baseline(tmp_path, monkeypatch):
    remote = tmp_path / "empty.git"
    git(tmp_path, "init", "--bare", str(remote))
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    baseline = release.empty_baseline(source, lambda *args: git(source, *args))
    # Compare to the exact worker commit-tree contract, not a second hard-coded expected hash.
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="PAL Arena",
        GIT_COMMITTER_NAME="PAL Arena",
        GIT_AUTHOR_EMAIL="arena@localhost",
        GIT_COMMITTER_EMAIL="arena@localhost",
        GIT_AUTHOR_DATE="2000-01-01T00:00:00+00:00",
        GIT_COMMITTER_DATE="2000-01-01T00:00:00+00:00",
    )
    tree = git(source, "mktree")
    expected = subprocess.check_output(
        ["git", "-c", "commit.gpgsign=false", "commit-tree", tree, "-m", "PAL Arena empty project baseline"],
        cwd=source,
        env=env,
        text=True,
    ).strip()
    assert baseline == expected
    git(source, "checkout", "-b", "arena/work/one", baseline)
    (source / "README.md").write_text("new project")
    git(source, "add", ".")
    git(source, "commit", "-m", "implemented")
    commit = git(source, "rev-parse", "HEAD")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "origin", "arena/work/one")
    original = release.checked_repo

    def local_repo(value):
        original(value)
        return str(remote)

    monkeypatch.setattr(release, "checked_repo", local_repo)
    job = {
        "project": {
            "id": "project",
            "repo": "https://github.com/team/new",
            "base_ref": "main",
            "revision": 1,
            "policy": {"auto_merge": True, "release_profile": "local"},
        },
        "task": {"id": "publisher", "attempt": 1},
        "dependencies": [
            {
                "status": "done",
                "revision": 1,
                "capability": "code",
                "review": {"decision": "accept", "evidence": ["Verified files"]},
                "evidence": {"commit": commit},
            }
        ],
    }
    config = {
        "release_profiles": {
            "local": {
                "validation_commands": [
                    [
                        sys.executable,
                        "-c",
                        'from pathlib import Path; assert Path("README.md").read_text()=="new project"',
                    ]
                ]
            }
        }
    }
    _, evidence = release.execute_release(job, config, tmp_path / "runs", lambda _: None, threading.Event())
    assert evidence["base_commit"] == baseline
    assert evidence["creates_target_branch"] is True
    assert git(remote, "rev-parse", "refs/heads/main") == evidence["commit"]
    git(remote, "merge-base", "--is-ancestor", commit, evidence["commit"])


def test_missing_base_does_not_publish_over_non_arena_repository(scenario):
    scenario["job"]["project"]["base_ref"] = "new-main"
    with pytest.raises(release.ReleaseError, match="并非 PAL Arena 新建项目"):
        execute(scenario)
    assert not git(scenario["remote"], "for-each-ref", "refs/heads/new-main")
