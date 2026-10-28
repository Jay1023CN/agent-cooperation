import httpx
import pytest

from agent_link.multica_runs import RunEvidenceError, fetch_verified_lineage, verified_lineage


def run(identifier, *, parent=None, status="failed", issue="issue", workspace="workspace", agent="agent"):
    return {
        "id": identifier,
        "status": status,
        "issue_id": issue,
        "workspace_id": workspace,
        "agent_id": agent,
        "attempt": 2 if parent else 1,
        "attribution": {"retry_of_task_id": parent} if parent else {},
    }


def verify(runs):
    return verified_lineage(runs, task_id="third", agent_id="agent", issue_id="issue", workspace_id="workspace")


def test_verified_lineage_follows_multiple_retry_links():
    runs = [run("first"), run("second", parent="first"), run("third", parent="second", status="running")]
    assert verify(runs) == {"lineage": "first", "task_id": "third", "agent_id": "agent", "depth": 2}


@pytest.mark.parametrize(
    "mutation", ["missing", "cycle", "wrong_issue", "missing_attribution", "wrong_agent", "stopped", "duplicate"]
)
def test_invalid_or_incomplete_run_evidence_fails_closed(mutation):
    runs = [run("first"), run("second", parent="first"), run("third", parent="second", status="running")]
    if mutation == "missing":
        runs.pop(0)
    elif mutation == "cycle":
        runs[0]["attribution"]["retry_of_task_id"] = "third"
    elif mutation == "wrong_issue":
        runs[0]["issue_id"] = "elsewhere"
    elif mutation == "missing_attribution":
        runs[1].pop("attribution")
    elif mutation == "wrong_agent":
        runs[2]["agent_id"] = "other"
    elif mutation == "stopped":
        runs[2]["status"] = "cancelled"
    else:
        runs.append(runs[0])
    with pytest.raises(RunEvidenceError):
        verify(runs)


def test_retry_attempt_without_origin_is_not_treated_as_a_new_root():
    broken = run("third", status="running")
    broken["attempt"] = 2
    with pytest.raises(RunEvidenceError, match="no origin"):
        verify([broken])
    broken.pop("attempt")
    with pytest.raises(RunEvidenceError, match="attempt"):
        verify([broken])


def test_fetch_uses_task_auth_and_rejects_redirects_and_remote_http():
    issue = "12345678-1234-1234-1234-123456789abc"
    runs = [run("third", status="running", issue=issue)]

    def handler(request):
        assert request.url.path == f"/api/issues/{issue}/task-runs"
        assert request.headers["authorization"] == "Bearer mat_secret"
        assert request.headers["x-workspace-id"] == "workspace"
        return httpx.Response(200, json=runs)

    args = {
        "server_url": "http://127.0.0.1:3000",
        "token": "mat_secret",
        "task_id": "third",
        "agent_id": "agent",
        "workspace_id": "workspace",
        "issue_id": issue,
    }
    assert fetch_verified_lineage(**args, transport=httpx.MockTransport(handler))["lineage"] == "third"
    with pytest.raises(RunEvidenceError):
        fetch_verified_lineage(**{**args, "server_url": "http://example.com"}, transport=httpx.MockTransport(handler))
    with pytest.raises(RunEvidenceError):
        fetch_verified_lineage(
            **args,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(302, headers={"Location": "https://example.com"})
            ),
        )
    for invalid in ("http://example.com", "https://user:password@example.com", "https://example.com/?token=secret"):
        with pytest.raises(RunEvidenceError):
            fetch_verified_lineage(**{**args, "server_url": invalid}, transport=httpx.MockTransport(handler))
    with pytest.raises(RunEvidenceError, match="task-scoped"):
        fetch_verified_lineage(**{**args, "token": "other"}, transport=httpx.MockTransport(handler))
    with pytest.raises(RunEvidenceError, match="Unable to read") as exc:
        fetch_verified_lineage(
            **args, transport=httpx.MockTransport(lambda request: httpx.Response(401, text="mat_secret"))
        )
    assert "mat_secret" not in str(exc.value)
