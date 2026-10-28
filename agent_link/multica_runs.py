"""Validate a full Multica issue task-runs response before local handoff."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit
from uuid import UUID

import httpx


class RunEvidenceError(ValueError):
    pass


def fetch_verified_lineage(
    *,
    server_url: str,
    token: str,
    task_id: str,
    agent_id: str,
    workspace_id: str,
    issue_id: str,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Read the unfiltered issue history with a task-scoped token, then verify it.

    The caller must obtain the identifiers from the current task's trusted
    environment. An HTTP response is an observation, not a reservation.
    """
    parsed = urlsplit(server_url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RunEvidenceError("Invalid Multica server URL")
    if parsed.scheme == "http":
        try:
            loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise RunEvidenceError("Non-loopback Multica servers require HTTPS")
    elif parsed.scheme != "https":
        raise RunEvidenceError("Multica server URL must use HTTPS or loopback HTTP")
    if not token.startswith("mat_"):
        raise RunEvidenceError("A Multica task-scoped token is required")
    try:
        issue_id = str(UUID(issue_id))
    except (ValueError, AttributeError):
        raise RunEvidenceError("A full Multica issue ID is required") from None
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, transport=transport) as client:
            response = client.get(
                server_url.rstrip("/") + "/api/issues/" + issue_id + "/task-runs",
                headers={"Authorization": "Bearer " + token, "X-Workspace-ID": workspace_id},
            )
            response.raise_for_status()
            runs = response.json()
    except (httpx.HTTPError, ValueError):
        raise RunEvidenceError("Unable to read Multica task history") from None
    return verified_lineage(runs, task_id=task_id, agent_id=agent_id, workspace_id=workspace_id, issue_id=issue_id)


def verified_lineage(runs: list, *, task_id: str, agent_id: str, workspace_id: str, issue_id: str) -> dict:
    """Trace explicit retry/rerun links; incomplete or ambiguous history fails closed.

    Input must come from the unfiltered issue task-runs endpoint. A caller-provided
    list alone does not prove its source or freshness, and this reserves nothing.
    """
    if not isinstance(runs, list) or not all((task_id, agent_id, workspace_id, issue_id)):
        raise RunEvidenceError("A complete task-runs list and task identity are required")
    by_id = {}
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("id"), str) or not run["id"]:
            raise RunEvidenceError("Malformed task-runs response")
        if run["id"] in by_id:
            raise RunEvidenceError("Duplicate task ID in task-runs response")
        by_id[run["id"]] = run
    current = by_id.get(task_id)
    if not current or current.get("agent_id") != agent_id or current.get("status") != "running":
        raise RunEvidenceError("Current agent task is missing or not running")
    seen = set()
    cursor = task_id
    while True:
        if cursor in seen:
            raise RunEvidenceError("Cycle in task retry lineage")
        seen.add(cursor)
        run = by_id.get(cursor)
        if not run or run.get("issue_id") != issue_id or run.get("workspace_id") != workspace_id:
            raise RunEvidenceError("Task lineage is missing or outside the expected issue and workspace")
        attribution = run.get("attribution")
        if not isinstance(attribution, dict):
            raise RunEvidenceError("Task attribution is unavailable; retry ancestry is unknown")
        retry = attribution.get("retry_of_task_id")
        rerun = attribution.get("rerun_of_task_id")
        attempt = run.get("attempt")
        if type(attempt) is not int or attempt < 1:
            raise RunEvidenceError("Task attempt is unavailable")
        if any(value is not None and not isinstance(value, str) for value in (retry, rerun)):
            raise RunEvidenceError("Invalid retry origin")
        if retry and rerun:
            raise RunEvidenceError("Task has ambiguous retry and rerun origins")
        parent = retry or rerun
        if parent is None or parent == "":
            if attempt > 1:
                raise RunEvidenceError("Retry attempt has no origin in the task history")
            return {"lineage": cursor, "task_id": task_id, "agent_id": agent_id, "depth": len(seen) - 1}
        if not isinstance(parent, str):
            raise RunEvidenceError("Invalid retry origin")
        cursor = parent
