import json

import pytest
from fastapi.testclient import TestClient

from arena.api import create_app


@pytest.fixture
def timeline(tmp_path):
    app = create_app(tmp_path / "arena")
    owner = (tmp_path / "arena" / "owner-token.txt").read_text()
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + owner
        project = client.post("/api/projects", json={"name": "Timeline"}).json()
        task = client.post(
            f'/api/projects/{project["id"]}/tasks', json={"title": "Build", "prompt": "Implement"}
        ).json()
        yield client, app.state.store, project, task


def test_timeline_includes_reviews_and_paginates_without_unrelated_logs(timeline):
    client, store, project, task = timeline
    with store.tx():
        other = store.create_task(project["id"], "Other", "Unrelated")
        review = store.create_task(project["id"], "Review", "Check", kind="reviewer", source_id=task["id"])
        for i in range(8):
            store.event(project["id"], task["id"], "log", f"output-{i}")
        store.event(project["id"], review["id"], "review", "independent review")
        store.event(project["id"], other["id"], "log", "unrelated-canary")
    route = f'/api/tasks/{task["id"]}/timeline'
    response = client.get(route, params={"limit": 3})
    assert response.status_code == 200
    latest = response.json()
    assert latest["events"][-1]["text"] == "independent review"
    assert latest["events"][-1]["task_kind"] == "reviewer"
    assert "unrelated-canary" not in json.dumps(latest)
    ids = [event["id"] for event in latest["events"]]
    # Newly appended events must not shift or duplicate an older page.
    with store.tx():
        store.event(project["id"], task["id"], "log", "new output")
    cursor = latest["next_before"]
    while cursor:
        page = client.get(route, params={"limit": 3, "before": cursor}).json()
        assert all(event["id"] < cursor for event in page["events"])
        ids = [event["id"] for event in page["events"]] + ids
        cursor = page["next_before"]
    assert ids == sorted(set(ids))
    assert len(ids) == 11  # Task and review creation, eight log entries, one review.


def test_private_timeline_remains_owner_only(timeline):
    client, store, project, _ = timeline
    private = client.post(
        f'/api/projects/{project["id"]}/messages', json={"content": "private-canary", "channel": "personal"}
    ).json()["task"]
    with store.tx():
        store.event(project["id"], private["id"], "log", "private-result-canary")
    route = f'/api/tasks/{private["id"]}/timeline'
    assert "private-result-canary" in client.get(route).text
    partner = client.post("/api/invites", json={"name": "Partner", "kind": "member"}).json()
    assert client.get(route, headers={"Authorization": "Bearer " + partner["token"]}).status_code == 404
    machine = client.post("/api/invites", json={"name": "Worker", "kind": "machine"}).json()
    assert client.get(route, headers={"Authorization": "Bearer " + machine["token"]}).status_code == 401
    client.headers.pop("Authorization")
    assert client.get(route).status_code == 401


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"before": 0}, {"before": 2**64}])
def test_timeline_rejects_unbounded_or_invalid_pages(timeline, params):
    client, _, _, task = timeline
    assert client.get(f'/api/tasks/{task["id"]}/timeline', params=params).status_code == 422
