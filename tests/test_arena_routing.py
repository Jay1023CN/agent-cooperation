"""Selection and context-freezing contracts without model calls or client processes."""

import time

import pytest

from arena.store import Store, encoded, uid


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "routing")
    yield instance
    instance.db.close()


def project(store, mode="balanced"):
    project_id = uid()
    with store.tx():
        store.db.execute(
            "INSERT INTO projects(id,name,brief,repo,base_ref,requirements,policy,created) VALUES(?,?,?,?,?,?,?,?)",
            (
                project_id,
                "Competition",
                "Build",
                "https://github.com/example/competition",
                "main",
                encoded(["Runs"]),
                encoded({"routing": mode}),
                time.time(),
            ),
        )
    return project_id


def machine(store, name, *, profile=None, roles=None, readiness=None, capabilities=None, preference=10):
    machine_id = uid()
    with store.tx():
        result = store.register(
            machine_id,
            name,
            "fixture",
            1,
            [
                {
                    "key": "stable",
                    "name": name,
                    "provider": "fixture",
                    "model": name + " model",
                    "roles": roles or ["worker"],
                    "capabilities": capabilities or ["code"],
                    "routing_profile": profile or {},
                    "readiness": readiness or {},
                    "preference": preference,
                }
            ],
        )
    return machine_id, result[name]


def task(store, project_id, **fields):
    with store.tx():
        return store.create_task(project_id, "Task", "Do the work", **fields)


def claim(store, machine_id):
    with store.tx():
        return store.claim(machine_id)


def memory(store, scope, scope_id, content, *, status="active", category="constraint"):
    memory_id = uid()
    with store.tx():
        now = time.time()
        store.db.execute(
            "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (memory_id, scope, scope_id, category, content, status, 1, None, "author", now, now),
        )
    return memory_id


@pytest.mark.parametrize("polling", [("budget", "quality"), ("quality", "budget")])
def test_global_routing_selects_best_quality_independent_of_poll_order(store, polling):
    project_id = project(store, "quality")
    quality = machine(store, "quality", profile={"quality": 5, "speed": 3, "economy": 1})
    budget = machine(store, "budget", profile={"quality": 2, "speed": 3, "economy": 5})
    machines = {"quality": quality, "budget": budget}
    original = task(store, project_id)
    results = {name: claim(store, machines[name][0]) for name in polling}
    assert results["budget"] is None
    assert results["quality"]["task"]["id"] == original["id"]
    assert results["quality"]["agent"]["id"] == quality[1]
    assert "quality" in results["quality"]["task"]["routing_reason"]


@pytest.mark.parametrize("mode,expected", [("quality", "quality"), ("economy", "budget")])
def test_project_quality_and_economy_policies_choose_different_profiles(store, mode, expected):
    project_id = project(store, mode)
    quality = machine(store, "quality", profile={"quality": 5, "speed": 3, "economy": 1})
    budget = machine(store, "budget", profile={"quality": 2, "speed": 3, "economy": 5})
    task(store, project_id)
    results = {"quality": claim(store, quality[0]), "budget": claim(store, budget[0])}
    assert results[expected] is not None
    assert results["budget" if expected == "quality" else "quality"] is None
    assert "未推算真实价格" in results[expected]["task"]["routing_reason"]


@pytest.mark.parametrize(
    "profile,readiness,task_fields",
    [
        ({"quality": 5}, {"dispatchable": False}, {}),
        ({"quality": 5, "quota_state": "exhausted"}, {"dispatchable": True}, {}),
        ({"quality": 5, "paused_until": 9_999_999_999}, {"dispatchable": True}, {}),
        ({"quality": 5, "max_difficulty": "routine"}, {"dispatchable": True}, {"difficulty": "deep"}),
        ({"quality": 5, "context_window": 1000}, {"dispatchable": True}, {"context_tokens": 2000}),
    ],
)
def test_unavailable_profiles_are_excluded_before_scoring(store, profile, readiness, task_fields):
    project_id = project(store, "quality")
    unavailable, _ = machine(store, "high-score unavailable", profile=profile, readiness=readiness, preference=1)
    fallback, fallback_id = machine(
        store,
        "available fallback",
        profile={"quality": 1, "context_window": 10_000, "max_difficulty": "deep"},
        readiness={"dispatchable": True},
        preference=50,
    )
    task(store, project_id, **task_fields)
    assert claim(store, unavailable) is None
    assert claim(store, fallback)["agent"]["id"] == fallback_id


def test_requested_agent_never_falls_back_to_another_available_agent(store):
    project_id = project(store)
    requested, requested_id = machine(store, "requested", profile={"quota_state": "exhausted"})
    fallback, _ = machine(store, "fallback", profile={"quality": 5})
    original = task(store, project_id, requested_agent=requested_id)
    assert claim(store, requested) is None
    assert claim(store, fallback) is None
    assert store.task(original["id"])["status"] == "queued"
    with store.tx():
        store.db.execute(
            "UPDATE agents SET routing_profile=? WHERE id=?", (encoded({"quota_state": "available"}), requested_id)
        )
    assert claim(store, requested)["task"]["id"] == original["id"]


def test_best_scoring_author_cannot_review_own_work(store):
    project_id = project(store, "quality")
    author, author_id = machine(store, "author", roles=["worker", "reviewer"], profile={"quality": 5, "speed": 5})
    reviewer, reviewer_id = machine(store, "independent", roles=["reviewer"], profile={"quality": 1, "speed": 1})
    original = task(store, project_id)
    execution = claim(store, author)
    with store.tx():
        store.complete(execution["run_token"], "Implemented", {"commit": "a" * 40, "delivery": "github"})
    assert claim(store, author) is None
    accepted = claim(store, reviewer)
    assert accepted["task"]["source_id"] == original["id"]
    assert accepted["agent"]["id"] == reviewer_id
    assert accepted["agent"]["id"] != author_id


def test_requested_agent_cannot_override_reviewer_independence(store):
    project_id = project(store)
    author, author_id = machine(store, "author", roles=["worker", "reviewer"])
    reviewer, _ = machine(store, "independent", roles=["reviewer"])
    task(store, project_id)
    execution = claim(store, author)
    with store.tx():
        store.complete(execution["run_token"], "Implemented", {})
        verification = store.one("SELECT id FROM tasks WHERE kind='reviewer'")
        store.db.execute("UPDATE tasks SET requested_agent=? WHERE id=?", (author_id, verification["id"]))
    assert claim(store, author) is None
    assert claim(store, reviewer) is None
    assert store.task(verification["id"])["status"] == "queued"


def test_requested_agent_cannot_override_required_capability(store):
    project_id = project(store)
    code_machine, code_agent = machine(store, "code", capabilities=["code"], profile={"quality": 5})
    blender, _ = machine(store, "blender", capabilities=["blender"])
    original = task(store, project_id, requested_agent=code_agent, capability="blender")
    assert claim(store, code_machine) is None
    assert claim(store, blender) is None
    assert store.task(original["id"])["status"] == "queued"


def test_frozen_memory_selection_is_project_and_member_scoped(store):
    first = project(store)
    second = project(store)
    project_memory = memory(store, "project", first, "first project context")
    memory(store, "project", second, "other project secret")
    alice_memory = memory(store, "member", "alice", "alice preference")
    memory(store, "member", "bob", "bob private preference")
    memory(store, "project", first, "unapproved suggestion", status="proposed")
    memory(store, "project", first, "archived context", status="archived")
    executor, _ = machine(store, "executor")
    shared = task(store, first)
    shared_run = claim(store, executor)
    assert {m["id"] for m in shared_run["task"]["memory_snapshot"]} == {project_memory}
    with store.tx():
        store.complete(shared_run["run_token"], "fixture stop", {}, failed=True)
    private = task(store, first, channel="personal:alice")
    private_run = claim(store, executor)
    assert private_run["task"]["id"] == private["id"]
    assert {m["id"] for m in private_run["task"]["memory_snapshot"]} == {project_memory, alice_memory}
    assert store.task(shared["id"])["memory_snapshot"] == shared_run["task"]["memory_snapshot"]


def test_active_run_keeps_frozen_memory_until_explicit_refresh_or_new_attempt(store):
    project_id = project(store)
    memory_id = memory(store, "project", project_id, "Original rule")
    executor, _ = machine(store, "executor")
    original = task(store, project_id)
    first = claim(store, executor)
    frozen = first["task"]["memory_snapshot"]
    assert frozen[0]["content"] == "Original rule" and frozen[0]["revision"] == 1
    with store.tx():
        store.db.execute(
            "UPDATE memories SET content='Revised rule',revision=2,updated=? WHERE id=?", (time.time(), memory_id)
        )
    assert store.active_memories(project_id)[0]["content"] == "Revised rule"
    assert store.task(original["id"])["memory_snapshot"] == frozen
    assert store.active_run(first["run_token"])["memory_snapshot"] == frozen
    with store.tx():
        store.complete(first["run_token"], "retry required", {}, failed=True)
        store.db.execute("UPDATE tasks SET status='queued' WHERE id=?", (original["id"],))
    second = claim(store, executor)
    assert second["task"]["attempt"] == 2
    assert second["task"]["memory_snapshot"][0]["content"] == "Revised rule"
    assert second["task"]["memory_snapshot"][0]["revision"] == 2
    assert first["task"]["memory_snapshot"] == frozen


def local_commit(machine_id, *, delivery="local"):
    return {
        "commit": "a" * 40,
        "machine_id": machine_id,
        "workspace": "/fixture/workspaces/project/source/1",
        "delivery": delivery,
    }


def accepted_task(store, project_id, evidence):
    completed = task(store, project_id)
    with store.tx():
        store.db.execute(
            "UPDATE tasks SET status='done',evidence=?,review=? WHERE id=?",
            (
                encoded(evidence),
                encoded({"decision": "accept", "summary": "Verified", "evidence": ["Test passed"]}),
                completed["id"],
            ),
        )
    return completed


@pytest.mark.parametrize("delivery,expected", [("local", "local"), ("github", "remote")])
def test_source_locality_preserves_independent_review_and_allows_synchronized_work(store, delivery, expected):
    project_id = project(store, "quality")
    with store.tx():
        store.db.execute("UPDATE projects SET repo=? WHERE id=?", ("https://github.com/example/project", project_id))
    local, author_id = machine(store, "author", roles=["worker", "reviewer"], profile={"quality": 5})
    with store.tx():
        members = store.register(
            local,
            "author",
            "fixture",
            1,
            [
                {
                    "key": "stable",
                    "name": "author",
                    "provider": "fixture",
                    "roles": ["worker", "reviewer"],
                    "capabilities": ["code"],
                    "routing_profile": {"quality": 5},
                },
                {
                    "key": "independent",
                    "name": "local independent",
                    "provider": "fixture",
                    "roles": ["reviewer"],
                    "capabilities": ["code"],
                    "routing_profile": {"quality": 1},
                },
            ],
        )
    remote, remote_reviewer = machine(store, "remote reviewer", roles=["reviewer"], profile={"quality": 5})
    original = task(store, project_id)
    work = claim(store, local)
    assert work["agent"]["id"] == author_id
    with store.tx():
        store.complete(work["run_token"], "Implemented", local_commit(local, delivery=delivery))
    assignments = {"remote": claim(store, remote), "local": claim(store, local)}
    review = assignments[expected]
    assert review is not None
    assert assignments["remote" if expected == "local" else "local"] is None
    assert review["task"]["source_id"] == original["id"]
    assert review["agent"]["id"] == (members["local independent"] if expected == "local" else remote_reviewer)
    assert review["agent"]["id"] != author_id


@pytest.mark.parametrize("channel", ["shared", "personal:alice"])
@pytest.mark.parametrize("delivery,expected", [("local", "local"), ("github", "remote")])
def test_accepted_shared_dependency_locality_applies_to_shared_and_private_consumers(
    store, channel, delivery, expected
):
    project_id = project(store, "quality")
    with store.tx():
        store.db.execute("UPDATE projects SET repo=? WHERE id=?", ("https://github.com/example/project", project_id))
    local, _ = machine(store, "local", profile={"quality": 1})
    remote, _ = machine(store, "remote", profile={"quality": 5})
    dependency = accepted_task(store, project_id, local_commit(local, delivery=delivery))
    consumer = task(store, project_id, channel=channel, dependencies=[dependency["id"]])
    assignments = {"remote": claim(store, remote), "local": claim(store, local)}
    assert assignments[expected] is not None
    assert assignments[expected]["task"]["id"] == consumer["id"]
    assert assignments["remote" if expected == "local" else "local"] is None


@pytest.mark.parametrize("delivery,expected", [("local", "local"), ("github", "remote")])
def test_retry_respects_its_previous_commit_locality(store, delivery, expected):
    project_id = project(store, "quality")
    with store.tx():
        store.db.execute("UPDATE projects SET repo=? WHERE id=?", ("https://github.com/example/project", project_id))
    local, _ = machine(store, "local", profile={"quality": 1})
    original = task(store, project_id)
    first = claim(store, local)
    with store.tx():
        store.complete(first["run_token"], "Needs retry", local_commit(local, delivery=delivery), failed=True)
        store.db.execute("UPDATE tasks SET status='queued' WHERE id=?", (original["id"],))
    remote, _ = machine(store, "remote", profile={"quality": 5})
    assignments = {"remote": claim(store, remote), "local": claim(store, local)}
    assert assignments[expected] is not None
    assert assignments[expected]["task"]["id"] == original["id"]
    assert assignments[expected]["task"]["attempt"] == 2
    assert assignments["remote" if expected == "local" else "local"] is None


def test_dependencies_local_to_different_machines_wait_for_synchronization(store):
    project_id = project(store)
    first_machine, _ = machine(store, "first")
    second_machine, _ = machine(store, "second")
    first = accepted_task(store, project_id, local_commit(first_machine))
    second = accepted_task(store, project_id, local_commit(second_machine))
    consumer = task(store, project_id, dependencies=[first["id"], second["id"]])
    assert claim(store, first_machine) is None
    assert claim(store, second_machine) is None
    assert store.task(consumer["id"])["status"] == "queued"


def test_retry_after_repository_removed_requires_previous_workspace_machine(store):
    project_id = project(store, "quality")
    local, _ = machine(store, "local", profile={"quality": 1})
    original = task(store, project_id)
    first = claim(store, local)
    with store.tx():
        store.complete(first["run_token"], "Needs retry", local_commit(local, delivery="github"), failed=True)
        store.db.execute("UPDATE projects SET repo='' WHERE id=?", (project_id,))
        store.db.execute("UPDATE tasks SET status='queued' WHERE id=?", (original["id"],))
    remote, _ = machine(store, "remote", profile={"quality": 5})
    assert claim(store, remote) is None
    resumed = claim(store, local)
    assert resumed is not None
    assert resumed["task"]["id"] == original["id"]
