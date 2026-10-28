from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_link.handoff_store import HandoffError, HandoffStore


def test_one_claim_survives_restart_and_retry_is_not_reissued(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    assert store.create("root", "task-1", {"workspace": "project"}) == "pending"

    def attempt():
        try:
            return HandoffStore(path).claim("root")
        except HandoffError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: attempt(), range(8)))
    claimed = [x for x in claims if x]
    assert len(claimed) == 1
    store = HandoffStore(path)
    assert store.inspect("root")["state"] == "claimed"
    with pytest.raises(HandoffError):
        store.create("root", "retry-task", {"workspace": "project"})
    with pytest.raises(HandoffError):
        store.claim("root")
    store.submit("root", claimed[0]["handle"], {"summary": "candidate"})
    store.submit("root", claimed[0]["handle"], {"summary": "candidate"})
    assert store.inspect("root")["result"] == {"summary": "candidate"}
    with pytest.raises(HandoffError):
        store.submit("root", claimed[0]["handle"], {"summary": "changed"})


def test_idempotent_create_and_wrong_handle(tmp_path):
    store = HandoffStore(tmp_path / "db")
    assert store.create("root", "task", {"prompt": "hello"}) == "pending"
    assert store.create("root", "task", {"prompt": "hello"}) == "pending"
    with pytest.raises(HandoffError):
        store.create("other", "task", {"prompt": "hello"})
    store.claim("root")
    with pytest.raises(HandoffError):
        store.submit("root", "wrong", {"summary": "done"})
