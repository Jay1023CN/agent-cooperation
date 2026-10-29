"""Run-scoped MCP tools used by a subscription CLI inside an Arena task."""

from __future__ import annotations

import os
from urllib.parse import urlencode

from mcp.server.fastmcp import FastMCP

from .worker import API, safe_component


def build_server(api: API):
    mcp = FastMCP("pal_arena")

    @mcp.tool()
    def context() -> dict:
        """Read the current project requirements, team, dependency outputs and task state."""
        return api.request("GET", "/api/runs/context")

    @mcp.tool()
    def create_task(
        title: str,
        prompt: str,
        capability: str = "code",
        acceptance: list[str] | None = None,
        dependencies: list[str] | None = None,
        requested_agent: str | None = None,
        difficulty: str = "standard",
        context_tokens: int = 0,
        request_key: str | None = None,
    ) -> dict:
        """Delegate a bounded child task. State scope, deliverables and verification commands.

        Dependencies are task IDs in this project. The server enforces role and project scope.
        """
        return api.request(
            "POST",
            "/api/runs/tasks",
            {
                "title": title,
                "prompt": prompt,
                "capability": capability,
                "kind": "worker",
                "acceptance": acceptance or [],
                "dependencies": dependencies or [],
                "requested_agent": requested_agent,
                "difficulty": difficulty,
                "context_tokens": context_tokens,
                "client_request_id": request_key,
            },
        )

    @mcp.tool()
    def memories(refresh: bool = False) -> dict:
        """Read the bounded approved memory snapshot for this run.

        Set refresh=true only when an explicit update is needed; it replaces the run's pinned snapshot.
        Requirements outrank task instructions, which outrank project memory and member preferences.
        """
        suffix = "?refresh=true" if refresh else ""
        return api.request("GET", "/api/runs/memories" + suffix)

    @mcp.tool()
    def search_memories(query: str) -> dict:
        """Find relevant older memories beyond the initial snapshot; search within the current discussion's scope."""
        return api.request("GET", "/api/runs/memories/search?" + urlencode({"q": query}))

    @mcp.tool()
    def consolidate_memories(memories: list[dict], content: str, category: str = "context") -> dict:
        """Merge related memories in the same scope, retaining the original entries for inspection.

        Each item supplies id and revision. Preserve context and distinct details in the concise merged content.
        """
        return api.request(
            "POST", "/api/runs/memories/consolidate", {"memories": memories, "content": content, "category": category}
        )

    @mcp.tool()
    def propose_memory(content: str, category: str = "context") -> dict:
        """Propose durable context for human confirmation; never store credentials or unverified guesses.

        The server scopes personal discussions to member memory and shared work to project memory.
        A proposal does not become active until a member confirms it.
        """
        return api.request("POST", "/api/runs/memories", {"content": content, "category": category})

    @mcp.tool()
    def remember(
        content: str,
        category: str = "context",
        scope: str = "project",
        memory_id: str | None = None,
        expected_revision: int | None = None,
        request_key: str | None = None,
    ) -> dict:
        """Remember a durable preference, background, decision or constraint from this member conversation.

        Use member scope for personal details and project scope for shared project context. Update an existing
        matching memory by providing its id and revision; avoid duplicates and do not store credentials.
        """
        return api.request(
            "POST",
            "/api/runs/automatic-memory",
            {
                "content": content,
                "category": category,
                "scope": scope,
                "memory_id": memory_id,
                "expected_revision": expected_revision,
                "client_request_id": request_key,
            },
        )

    @mcp.tool()
    def recover_task(task_id: str, action: str, requested_agent: str | None = None) -> dict:
        """Leader only: retry or cancel a safely recoverable failed task in this discussion.

        Retry is bounded by the server. Unknown outcomes, interrupted runs and releases need a human check.
        requested_agent selects a compatible replacement, or null clears the prior agent preference.
        """
        body = {"action": action}
        if action == "retry":
            body["requested_agent"] = requested_agent
        return api.request(
            "POST",
            "/api/runs/tasks/" + safe_component(task_id) + "/recover",
            body,
        )

    @mcp.tool()
    def update_requirements(requirements: list[str], expected_revision: int | None = None) -> dict:
        """Leader only: save an agreed checklist with optimistic concurrency protection.

        Read context first and provide its project revision when deliberately changing the latest checklist.
        Omission uses this task's pinned revision, so a stale task cannot overwrite a partner's new requirements.
        """
        body = {"requirements": requirements}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        return api.request("POST", "/api/runs/requirements", body)

    @mcp.tool()
    def report_review(decision: str, summary: str, evidence: list[str]) -> dict:
        """Reviewer only: accept or changes_requested, with evidence from actual files and checks.

        Do not accept based solely on the author's report. The server rejects empty evidence.
        """
        return api.request("POST", "/api/runs/review", {"decision": decision, "summary": summary, "evidence": evidence})

    @mcp.tool()
    def finish_project(summary: str) -> dict:
        """Leader only: request delivery after all current-revision work has independent accepted reviews."""
        return api.request("POST", "/api/runs/finish", {"summary": summary})

    return mcp


def main():
    api = API(
        os.environ.get("ARENA_SERVER", "http://127.0.0.1:8765"),
        os.environ.get("ARENA_RUN_TOKEN", ""),
        os.environ.get("ARENA_ALLOW_HTTP") == "1",
    )
    try:
        build_server(api).run(transport="stdio")
    finally:
        api.close()


if __name__ == "__main__":
    main()
