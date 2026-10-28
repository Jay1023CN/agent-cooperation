import threading

from agent_link.handoff_store import HandoffStore
from agent_link.lease_keeper import LeaseKeeper


def test_background_renews_then_expires_after_verification_failure(tmp_path):
    store = HandoffStore(tmp_path / "db")
    store.create("root", "run", {"prompt": "test"})
    seen = threading.Event()
    calls = []

    def verify(task, issue):
        calls.append((task, issue))
        if len(calls) > 1:
            seen.set()
            raise ValueError("run cancelled")
        return {"lineage": "root"}

    def check(lineage, task, instance):
        assert instance == store.instance_id()

    keeper = LeaseKeeper(store, verify, check, interval=0.03)
    keeper.start("root", "run", "issue")
    worker = keeper.workers["root"][1]
    assert seen.wait(5)
    worker.join(5)
    assert len(calls) == 2
    assert store.inspect("root")["state"] == "expired"
    assert HandoffStore(tmp_path / "db").inspect("root")["state"] == "expired"
    assert "root" not in keeper.workers
    keeper.close()


def test_lease_refresh_checks_coordinator_before_renewing(tmp_path):
    store = HandoffStore(tmp_path / "db")
    store.create("root", "run", {})
    checked = []
    keeper = LeaseKeeper(
        store,
        lambda task, issue: {"lineage": "root"},
        lambda lineage, task, instance: checked.append((lineage, task, instance)),
    )
    before = store.inspect("root")["lease_until"]
    deadline = keeper.refresh("root", "run", "issue")
    assert deadline >= before
    assert checked == [("root", "run", store.instance_id())]
    keeper.close()
