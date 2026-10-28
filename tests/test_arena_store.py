"""State-machine checks use isolated stores and never launch external agents."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from arena.store import Conflict, Store, digest, encoded, uid


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "arena")
    yield instance
    instance.db.close()


def project(store, *, name="Project", requirements=None):
    project_id = uid()
    with store.tx():
        store.db.execute(
            "INSERT INTO projects(id,name,brief,repo,base_ref,requirements,created) VALUES(?,?,?,?,?,?,?)",
            (
                project_id,
                name,
                "Build a deliverable",
                "https://github.com/example/project",
                "main",
                encoded(requirements or ["Runs correctly"]),
                time.time(),
            ),
        )
    return project_id


def machine(store, name, *, slots=1, roles=None, capabilities=None, preference=10):
    identity = uid()
    with store.tx():
        agents = store.register(
            identity,
            name,
            "test-os",
            slots,
            [
                {
                    "key": "test-agent",
                    "name": name + " agent",
                    "provider": "fake",
                    "roles": roles or ["worker"],
                    "capabilities": capabilities or ["code"],
                    "preference": preference,
                }
            ],
        )
    return identity, next(iter(agents.values()))


def task(store, project_id, **kwargs):
    with store.tx():
        return store.create_task(project_id, kwargs.pop("title", "Implement"), "Do work", **kwargs)


def claim(store, machine_id):
    with store.tx():
        return store.claim(machine_id)


def complete(store, token, *, result="Implemented", evidence=None, failed=False):
    with store.tx():
        return store.complete(token, result, evidence or {"commit": "a" * 40, "delivery": "github"}, failed=failed)


def review(store, claim_result, decision="accept"):
    with store.tx():
        store.db.execute(
            "UPDATE tasks SET review=? WHERE id=?",
            (
                encoded({"decision": decision, "summary": "Checked the deliverable", "evidence": ["Tests passed"]}),
                claim_result["task"]["id"],
            ),
        )
    return complete(store, claim_result["run_token"], result="Reviewed")


def prepare_review(store):
    project_id = project(store)
    worker_id, worker_agent = machine(store, "worker", roles=["worker", "reviewer"])
    reviewer_id, reviewer_agent = machine(store, "reviewer", roles=["reviewer"])
    original = task(store, project_id)
    work = claim(store, worker_id)
    complete(store, work["run_token"])
    verification = claim(store, reviewer_id)
    return project_id, original, work, verification, worker_id, reviewer_id


def test_one_task_is_claimed_once_under_concurrent_pollers(store):
    project_id = project(store)
    machine_id, _ = machine(store, "one", slots=2)
    original = task(store, project_id)
    barrier = Barrier(2)

    def poll():
        barrier.wait()
        return claim(store, machine_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(poll) for _ in range(2)]
        claims = [future.result() for future in futures]
    assert len([value for value in claims if value]) == 1
    assert store.task(original["id"])["attempt"] == 1


def test_queued_conversation_adopts_latest_spec_before_start(store):
    project_id = project(store)
    machine_id, _ = machine(store, "leader", roles=["leader"], capabilities=["planning"])
    task(store, project_id, kind="leader", capability="planning")
    with store.tx():
        store.db.execute(
            "UPDATE projects SET revision=2,requirements=? WHERE id=?", (encoded(["Latest agreement"]), project_id)
        )
    run = claim(store, machine_id)
    assert run["task"]["revision"] == 2
    assert run["task"]["acceptance"] == ["Latest agreement"]
    assert complete(store, run["run_token"], result="Acknowledged current requirements")["status"] == "done"


def test_capacity_limits_machine_even_with_two_agents(store):
    project_id = project(store)
    machine_id, _ = machine(store, "one", slots=1)
    with store.tx():
        store.register(
            machine_id,
            "one",
            "test-os",
            1,
            [
                {"key": key, "name": key, "provider": "fake", "roles": ["worker"], "capabilities": ["code"]}
                for key in ("first", "second")
            ],
        )
    task(store, project_id, title="First")
    task(store, project_id, title="Second")
    assert claim(store, machine_id) is not None
    assert claim(store, machine_id) is None


def test_lower_cost_preference_wins_independent_of_polling_order(store):
    project_id = project(store)
    expensive, _ = machine(store, "expensive", preference=50)
    cheap, cheap_agent = machine(store, "cheap", preference=1)
    task(store, project_id)
    assert claim(store, expensive) is None
    assigned = claim(store, cheap)
    assert assigned["agent"]["id"] == cheap_agent


def test_offline_preferred_agent_does_not_starve_available_agent(store):
    project_id = project(store)
    offline, _ = machine(store, "offline", preference=1)
    online, _ = machine(store, "online", preference=20)
    with store.tx():
        store.db.execute("UPDATE machines SET seen=? WHERE id=?", (time.time() - 60, offline))
    task(store, project_id)
    assert claim(store, online) is not None


def test_dependencies_wait_for_independent_review_not_worker_exit(store):
    project_id = project(store)
    worker_id, _ = machine(store, "worker")
    reviewer_id, _ = machine(store, "reviewer", roles=["reviewer"])
    first = task(store, project_id)
    second = task(store, project_id, title="Dependent", dependencies=[first["id"]])
    work = claim(store, worker_id)
    complete(store, work["run_token"])
    assert store.task(first["id"])["status"] == "in_review"
    assert claim(store, worker_id) is None
    verification = claim(store, reviewer_id)
    assert verification["source"]["evidence"]["commit"] == "a" * 40
    review(store, verification)
    assert claim(store, worker_id)["task"]["id"] == second["id"]


def test_worker_cannot_review_its_own_work_and_offline_review_waits(store):
    project_id = project(store)
    worker_id, _ = machine(store, "worker", roles=["worker", "reviewer"])
    original = task(store, project_id)
    work = claim(store, worker_id)
    complete(store, work["run_token"])
    assert claim(store, worker_id) is None
    assert store.task(original["id"])["status"] == "in_review"
    review_tasks = [t for t in store.snapshot(project_id)["tasks"] if t["kind"] == "reviewer"]
    assert len(review_tasks) == 1
    assert review_tasks[0]["status"] == "queued"


def test_review_requires_matching_task_capability(store):
    project_id = project(store)
    worker_id, _ = machine(store, "artist", capabilities=["blender"])
    wrong_reviewer, _ = machine(store, "code reviewer", roles=["reviewer"], preference=1)
    correct_reviewer, _ = machine(
        store, "blender reviewer", roles=["reviewer"], capabilities=["blender"], preference=20
    )
    task(store, project_id, capability="blender")
    complete(store, claim(store, worker_id)["run_token"])
    assert claim(store, wrong_reviewer) is None
    assert claim(store, correct_reviewer) is not None


def test_expired_lease_is_interrupted_and_old_results_cannot_commit(store):
    project_id = project(store)
    machine_id, _ = machine(store, "worker")
    original = task(store, project_id)
    work = claim(store, machine_id)
    with store.tx():
        store.db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time.time() - 1, original["id"]))
    assert claim(store, machine_id) is None
    assert store.task(original["id"])["status"] == "interrupted"
    with pytest.raises(Conflict):
        complete(store, work["run_token"])
    assert store.task(original["id"])["attempt"] == 1


def test_cancelled_running_task_rejects_late_completion(store):
    project_id = project(store)
    machine_id, _ = machine(store, "worker")
    original = task(store, project_id)
    work = claim(store, machine_id)
    with store.tx():
        store.db.execute("UPDATE tasks SET status='cancelled',lease_hash=NULL WHERE id=?", (original["id"],))
    with pytest.raises(Conflict):
        complete(store, work["run_token"])
    assert store.task(original["id"])["status"] == "cancelled"


def test_review_cannot_revive_cancelled_source(store):
    _, original, _, verification, _, _ = prepare_review(store)
    with store.tx():
        store.db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (original["id"],))
    try:
        review(store, verification)
    except Conflict:
        pass
    assert store.task(original["id"])["status"] == "cancelled"


def test_revision_change_blocks_old_work_success(store):
    project_id = project(store)
    machine_id, _ = machine(store, "worker")
    original = task(store, project_id)
    work = claim(store, machine_id)
    with store.tx():
        store.db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (project_id,))
    assert complete(store, work["run_token"])["status"] == "changes_requested"
    assert store.task(original["id"])["status"] == "changes_requested"
    assert not [t for t in store.snapshot(project_id)["tasks"] if t["kind"] == "reviewer"]


def test_revision_change_during_review_does_not_strand_source_in_review(store):
    project_id, original, _, verification, _, _ = prepare_review(store)
    with store.tx():
        store.db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (project_id,))
    review(store, verification)
    assert store.task(original["id"])["status"] in ("changes_requested", "queued")
    assert store.project(project_id)["status"] != "ready"


def test_review_is_required_even_if_agent_says_all_tests_passed(store):
    _, original, _, verification, _, _ = prepare_review(store)
    response = complete(store, verification["run_token"], result="Everything passed, accept it")
    assert response["status"] == "blocked"
    assert store.task(original["id"])["status"] == "in_review"


def test_changes_requested_retries_at_most_three_attempts(store):
    project_id = project(store)
    worker_id, _ = machine(store, "worker")
    reviewer_id, _ = machine(store, "reviewer", roles=["reviewer"])
    original = task(store, project_id)
    for attempt in range(1, 4):
        work = claim(store, worker_id)
        assert work["task"]["attempt"] == attempt
        complete(store, work["run_token"])
        verification = claim(store, reviewer_id)
        review(store, verification, decision="changes_requested")
    assert store.task(original["id"])["status"] == "changes_requested"
    assert claim(store, worker_id) is None


def test_cross_project_dependencies_and_parent_are_rejected(store):
    first = project(store, name="First")
    second = project(store, name="Second")
    original = task(store, first)
    for relation in ("dependencies", "parent_id", "source_id"):
        value = [original["id"]] if relation == "dependencies" else original["id"]
        with pytest.raises(Conflict):
            task(store, second, **{relation: value})


def test_tokens_are_hashed_and_not_exposed_by_state(store):
    project_id = project(store)
    machine_id, _ = machine(store, "worker")
    with store.tx():
        identity = store.new_identity("Partner", "member")
    task(store, project_id)
    run = claim(store, machine_id)
    row = store.one("SELECT * FROM identities WHERE id=?", (identity["id"],))
    assert row["token_hash"] == digest(identity["token"])
    assert identity["token"] not in json.dumps(row)
    state = json.dumps(store.snapshot(project_id))
    assert identity["token"] not in state
    assert run["run_token"] not in state
    assert digest(run["run_token"]) not in state
    assert "lease_hash" not in state
    assert store.authenticate("unknown-token") is None
    assert store.authenticate(identity["token"])["id"] == identity["id"]


def test_private_messages_do_not_leak_to_other_members(store):
    project_id = project(store)
    with store.tx():
        for channel, content in (
            ("shared", "Shared context"),
            ("personal:alice", "Alice draft"),
            ("personal:bob", "Bob draft"),
        ):
            store.db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?)", (uid(), project_id, channel, content, channel, time.time())
            )
    alice = encoded(store.snapshot(project_id, channel="personal", author="alice"))
    bob = encoded(store.snapshot(project_id, channel="personal", author="bob"))
    shared = encoded(store.snapshot(project_id))
    assert "Alice draft" in alice and "Bob draft" not in alice
    assert "Bob draft" in bob and "Alice draft" not in bob
    assert "Alice draft" not in shared and "Bob draft" not in shared


def test_reviewer_task_requires_source(store):
    project_id = project(store)
    with pytest.raises(Conflict):
        task(store, project_id, kind="reviewer")


def test_completed_review_does_not_create_review_of_review(store):
    project_id, original, _, verification, _, _ = prepare_review(store)
    assert review(store, verification)["status"] == "done"
    all_tasks = store.snapshot(project_id)["tasks"]
    reviews = [t for t in all_tasks if t["kind"] == "reviewer"]
    assert len(reviews) == 1
    assert reviews[0]["source_id"] == original["id"]
    summaries = [t for t in all_tasks if t["kind"] == "leader"]
    assert len(summaries) == 1


def test_completion_can_enqueue_review_when_queue_at_capacity(store):
    project_id = project(store)
    worker_id, _ = machine(store, "worker")
    for index in range(40):
        task(store, project_id, title=f"Task {index}")
    work = claim(store, worker_id)
    assert complete(store, work["run_token"])["status"] == "in_review"
    assert len([t for t in store.snapshot(project_id)["tasks"] if t["kind"] == "reviewer"]) == 1


def test_review_acceptance_requires_nonempty_structured_evidence(store):
    _, original, _, verification, _, _ = prepare_review(store)
    with store.tx():
        store.db.execute(
            "UPDATE tasks SET review=? WHERE id=?",
            (
                encoded({"decision": "accept", "summary": "Looks good", "evidence": []}),
                verification["task"]["id"],
            ),
        )
    try:
        complete(store, verification["run_token"])
    except Conflict:
        pass
    assert store.task(original["id"])["status"] != "done"


def test_private_work_review_logs_and_results_stay_in_personal_channel(store):
    project_id = project(store)
    worker_id, _ = machine(store, "worker")
    reviewer_id, _ = machine(store, "reviewer", roles=["reviewer"])
    with store.tx():
        store.db.execute("UPDATE projects SET status='ready' WHERE id=?", (project_id,))
    original = task(store, project_id, title="PRIVATE_TASK", channel="personal:alice")
    assert store.project(project_id)["status"] == "ready"
    work = claim(store, worker_id)
    complete(store, work["run_token"], result="PRIVATE_RESULT")
    verification = claim(store, reviewer_id)
    assert verification["task"]["channel"] == "personal:alice"
    review(store, verification)
    with store.tx():
        store.db.execute(
            "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)",
            (uid(), project_id, original["id"], "PRIVATE_ARTIFACT", 4, 1, time.time()),
        )
    alice = encoded(store.snapshot(project_id, channel="personal", author="alice"))
    bob = encoded(store.snapshot(project_id, channel="personal", author="bob"))
    shared = encoded(store.snapshot(project_id))
    for marker in ("PRIVATE_TASK", "PRIVATE_RESULT", "PRIVATE_ARTIFACT"):
        assert marker in alice
        assert marker not in bob
        assert marker not in shared
    assert store.project(project_id)["status"] == "ready"


def test_personal_task_dependencies_do_not_cross_personal_channels(store):
    project_id = project(store)
    original = task(store, project_id, channel="personal:alice")
    for channel in ("shared", "personal:bob"):
        with pytest.raises(Conflict):
            task(store, project_id, dependencies=[original["id"]], channel=channel)


def test_delegating_worker_waits_for_reviewed_children_before_continuation(store):
    project_id = project(store)
    worker_id, _ = machine(store, "worker")
    reviewer_id, _ = machine(store, "reviewer", roles=["reviewer"])
    parent = task(store, project_id, title="Build combined feature")
    parent_run = claim(store, worker_id)
    dependent = task(store, project_id, title="Ship combined feature", dependencies=[parent["id"]])
    child = task(store, project_id, title="Child implementation", parent_id=parent["id"])
    outcome = complete(store, parent_run["run_token"], result="Delegated the implementation")
    assert outcome["status"] == "coordinating"
    assert store.task(parent["id"])["status"] == "coordinating"
    assert not [
        t for t in store.snapshot(project_id)["tasks"] if t["kind"] == "reviewer" and t["source_id"] == parent["id"]
    ]
    child_run = claim(store, worker_id)
    assert child_run["task"]["id"] == child["id"]
    complete(store, child_run["run_token"])
    review(store, claim(store, reviewer_id))
    assert store.task(parent["id"])["status"] == "queued"
    continuation = claim(store, worker_id)
    assert continuation["task"]["id"] == parent["id"]
    assert continuation["task"]["attempt"] == 2
    assert [t["id"] for t in continuation["dependencies"]] == [child["id"]]
    assert continuation["dependencies"][0]["evidence"]["commit"] == "a" * 40
    assert store.task(dependent["id"])["status"] == "queued"


def test_delegating_worker_waits_for_every_child_to_be_reviewed(store):
    project_id = project(store)
    worker_id, _ = machine(store, "worker")
    reviewer_id, _ = machine(store, "reviewer", roles=["reviewer"])
    parent = task(store, project_id, title="Parent")
    parent_run = claim(store, worker_id)
    first = task(store, project_id, title="First child", parent_id=parent["id"])
    second = task(store, project_id, title="Second child", parent_id=parent["id"])
    complete(store, parent_run["run_token"])
    first_run = claim(store, worker_id)
    assert first_run["task"]["id"] == first["id"]
    complete(store, first_run["run_token"])
    review(store, claim(store, reviewer_id))
    assert store.task(parent["id"])["status"] == "coordinating"
    second_run = claim(store, worker_id)
    assert second_run["task"]["id"] == second["id"]
    complete(store, second_run["run_token"])
    review(store, claim(store, reviewer_id))
    assert store.task(parent["id"])["status"] == "queued"


def test_revision_change_during_review_before_report_unblocks_source(store):
    project_id, original, _, verification, _, _ = prepare_review(store)
    with store.tx():
        store.db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (project_id,))
    complete(store, verification["run_token"], result="Review report rejected because requirements changed")
    assert store.task(original["id"])["status"] in ("changes_requested", "queued")
    assert store.project(project_id)["status"] != "ready"


def accepted_worker(store, project_id, *, channel="shared"):
    original = task(store, project_id, channel=channel)
    with store.tx():
        store.db.execute(
            "UPDATE tasks SET status='done',review=?,evidence=? WHERE id=?",
            (
                encoded({"decision": "accept", "summary": "Verified", "evidence": ["Tests passed"]}),
                encoded({"commit": "b" * 40, "branch": "arena/test", "delivery": "github"}),
                original["id"],
            ),
        )
    return original


def release_task(store, project_id, workers, *, profile="site"):
    return task(
        store,
        project_id,
        title="Publish delivery",
        kind="publisher",
        capability=f"release:{profile}",
        dependencies=[w["id"] for w in workers],
    )


def test_publisher_requires_shared_channel(store):
    project_id = project(store)
    with pytest.raises(Conflict):
        task(store, project_id, kind="publisher", capability="release:site", channel="personal:alice")


def test_publisher_profile_capability_controls_machine_selection(store):
    project_id = project(store)
    original = accepted_worker(store, project_id)
    wrong, _ = machine(store, "other release", roles=["publisher"], capabilities=["release:other"], preference=1)
    right, right_agent = machine(
        store, "site release", roles=["publisher"], capabilities=["release:site"], preference=20
    )
    release = release_task(store, project_id, [original])
    assert claim(store, wrong) is None
    run = claim(store, right)
    assert run["task"]["id"] == release["id"]
    assert run["agent"]["id"] == right_agent
    assert run["dependencies"][0]["evidence"]["commit"] == "b" * 40


@pytest.mark.parametrize("broken_field", ["status", "revision", "review", "decision"])
def test_publisher_waits_for_current_accepted_workers(store, broken_field):
    project_id = project(store)
    original = accepted_worker(store, project_id)
    publisher, _ = machine(store, "publisher", roles=["publisher"], capabilities=["release:site"])
    release = release_task(store, project_id, [original])
    with store.tx():
        if broken_field == "status":
            store.db.execute("UPDATE tasks SET status='in_review' WHERE id=?", (original["id"],))
        elif broken_field == "revision":
            store.db.execute("UPDATE tasks SET revision=0 WHERE id=?", (original["id"],))
        elif broken_field == "review":
            store.db.execute("UPDATE tasks SET review=NULL WHERE id=?", (original["id"],))
        else:
            store.db.execute(
                "UPDATE tasks SET review=? WHERE id=?",
                (
                    encoded({"decision": "changes_requested", "summary": "Needs work", "evidence": ["Failed test"]}),
                    original["id"],
                ),
            )
    assert claim(store, publisher) is None
    assert store.task(release["id"])["status"] == "queued"


def test_publisher_waits_until_summary_leader_finishes(store):
    project_id = project(store)
    original = accepted_worker(store, project_id)
    leader, _ = machine(store, "leader", roles=["leader"], capabilities=["planning"])
    publisher, _ = machine(store, "publisher", roles=["publisher"], capabilities=["release:site"])
    summary = task(store, project_id, kind="leader", capability="planning")
    release = release_task(store, project_id, [original])
    assert claim(store, publisher) is None
    summary_run = claim(store, leader)
    assert summary_run["task"]["id"] == summary["id"]
    assert claim(store, publisher) is None
    complete(store, summary_run["run_token"], result="All requirements verified")
    assert claim(store, publisher)["task"]["id"] == release["id"]


def test_publisher_ignores_private_work_and_cancelled_scope(store):
    project_id = project(store)
    original = accepted_worker(store, project_id)
    cancelled = task(store, project_id, title="Abandoned scope")
    task(store, project_id, title="Private exploration", channel="personal:alice")
    with store.tx():
        store.db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (cancelled["id"],))
    publisher, _ = machine(store, "publisher", roles=["publisher"], capabilities=["release:site"])
    release = release_task(store, project_id, [original])
    assert claim(store, publisher)["task"]["id"] == release["id"]


@pytest.mark.parametrize("change", ["added", "removed", "extra_nonworker"])
def test_publisher_invalidates_changed_dependency_scope(store, change):
    project_id = project(store)
    original = accepted_worker(store, project_id)
    publisher, _ = machine(store, "publisher", roles=["publisher"], capabilities=["release:site"])
    if change == "extra_nonworker":
        extra = task(store, project_id, kind="leader", capability="planning")
        with store.tx():
            store.db.execute("UPDATE tasks SET status='done' WHERE id=?", (extra["id"],))
        release = release_task(store, project_id, [original, extra])
    else:
        release = release_task(store, project_id, [original])
        if change == "added":
            accepted_worker(store, project_id)
        else:
            with store.tx():
                store.db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (original["id"],))
    assert claim(store, publisher) is None
    assert store.task(release["id"])["status"] == "changes_requested"
    assert store.project(project_id)["status"] == "active"


def test_publisher_does_not_run_without_deliverable_workers(store):
    project_id = project(store)
    publisher, _ = machine(store, "publisher", roles=["publisher"], capabilities=["release:site"])
    release = release_task(store, project_id, [])
    assert claim(store, publisher) is None
    assert store.task(release["id"])["status"] == "queued"


def test_publisher_invalidates_stale_requirement_version(store):
    project_id = project(store)
    original = accepted_worker(store, project_id)
    publisher, _ = machine(store, "publisher", roles=["publisher"], capabilities=["release:site"])
    release = release_task(store, project_id, [original])
    with store.tx():
        store.db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (project_id,))
    assert claim(store, publisher) is None
    assert store.task(release["id"])["status"] == "changes_requested"


def test_running_publisher_freezes_shared_task_creation_but_not_private_work(store):
    project_id = project(store)
    original = accepted_worker(store, project_id)
    publisher, _ = machine(store, "publisher", roles=["publisher"], capabilities=["release:site"])
    release_task(store, project_id, [original])
    assert claim(store, publisher)
    for role in ("worker", "leader", "publisher"):
        with pytest.raises(Conflict):
            task(store, project_id, kind=role)
    assert task(store, project_id, channel="personal:alice")["status"] == "queued"
    other_project = project(store, name="Other competition")
    assert task(store, other_project)["status"] == "queued"


@pytest.mark.parametrize(
    "failed,project_status,task_status", [(False, "delivered", "done"), (True, "release_failed", "failed")]
)
def test_publisher_completion_updates_project_delivery_status(store, failed, project_status, task_status):
    project_id = project(store)
    original = accepted_worker(store, project_id)
    publisher, _ = machine(store, "publisher", roles=["publisher"], capabilities=["release:site"])
    release = release_task(store, project_id, [original])
    run = claim(store, publisher)
    result = complete(
        store, run["run_token"], result="Release result", evidence={"url": "http://localhost:3000"}, failed=failed
    )
    assert result["status"] == task_status
    assert store.task(release["id"])["status"] == task_status
    assert store.project(project_id)["status"] == project_status
    assert not [t for t in store.snapshot(project_id)["tasks"] if t["kind"] == "reviewer"]


def test_publishers_for_same_project_are_serialized(store):
    project_id = project(store)
    original = accepted_worker(store, project_id)
    first, _ = machine(store, "first publisher", roles=["publisher"], capabilities=["release:site"], preference=1)
    second, _ = machine(store, "second publisher", roles=["publisher"], capabilities=["release:site"], preference=20)
    release_task(store, project_id, [original])
    release_task(store, project_id, [original])
    assert claim(store, first)
    assert claim(store, second) is None


def test_publishers_for_different_projects_can_run_concurrently(store):
    first_project = project(store, name="Competition one")
    second_project = project(store, name="Competition two")
    first_work = accepted_worker(store, first_project)
    second_work = accepted_worker(store, second_project)
    first, _ = machine(store, "first publisher", roles=["publisher"], capabilities=["release:site"], preference=1)
    second, _ = machine(store, "second publisher", roles=["publisher"], capabilities=["release:site"], preference=20)
    first_release = release_task(store, first_project, [first_work])
    second_release = release_task(store, second_project, [second_work])
    assert claim(store, first)["task"]["id"] == first_release["id"]
    assert claim(store, second)["task"]["id"] == second_release["id"]
