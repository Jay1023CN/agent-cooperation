import asyncio
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_link.coordination import ReservationRequest, ReservationStore, build_app
from agent_link.coordination_client import CoordinationClient, CoordinationError
from agent_link.desktop_handoff import build_server
from agent_link.handoff_store import HandoffStore


def test_atomic_reservation_cannot_move_to_other_machine_or_instance(tmp_path):
    path = tmp_path / "central.db"
    store = ReservationStore(path)
    request = ReservationRequest(workspace_id="w", lineage="root", task_id="run", instance_id="install-a")

    def reserve(machine):
        try:
            return ReservationStore(path).reserve(request, machine)["status"]
        except ValueError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(reserve, ["machine-a", "machine-b"]))
    assert sorted(result) == ["blocked", "reserved"]
    owner = store.inspect("w", "root")["machine"]
    assert ReservationStore(path).reserve(request, owner)["status"] == "reserved"
    with pytest.raises(ValueError):
        store.reserve(request.model_copy(update={"instance_id": "install-b"}), owner)
    with pytest.raises(ValueError):
        store.reserve(request.model_copy(update={"task_id": "retry"}), owner)
    with pytest.raises(ValueError):
        store.reserve(request.model_copy(update={"lineage": "another"}), owner)


def test_auth_workspace_scoping_and_no_secret_leaks(tmp_path):
    token_a, token_b = "a" * 32, "b" * 32
    app = build_app(
        ReservationStore(tmp_path / "db"),
        {
            "a": {"token": token_a, "workspaces": ["w"]},
            "b": {"token": token_b, "workspaces": ["other"]},
        },
    )
    body = {"workspace_id": "w", "lineage": "root", "task_id": "run", "instance_id": "instance"}
    with TestClient(app) as client:
        assert client.post("/v1/reservations", json=body).status_code == 401
        assert (
            client.post("/v1/reservations", json=body, headers={"Authorization": f"Bearer {token_b}"}).status_code
            == 403
        )
        headers = {"Authorization": f"Bearer {token_a}"}
        response = client.post("/v1/reservations", json=body, headers=headers)
        assert response.status_code == 200
        assert token_a not in response.text
        assert client.post("/v1/reservations", json=body, headers=headers).json() == response.json()
        assert client.post("/v1/reservations/check", json=body, headers=headers).status_code == 200
        assert (
            client.post("/v1/reservations/check", json={**body, "instance_id": "other"}, headers=headers).status_code
            == 409
        )
        assert client.post("/v1/reservations", json={**body, "task_id": "retry"}, headers=headers).status_code == 409
        assert (
            client.post(
                "/v1/reservations/inspect", json={"workspace_id": "w", "lineage": "root"}, headers=headers
            ).json()["machine"]
            == "a"
        )


def test_invalid_configuration_and_duplicate_tokens_are_rejected(tmp_path):
    store = ReservationStore(tmp_path / "db")
    with pytest.raises(ValueError):
        build_app(store, {})
    with pytest.raises(ValueError):
        build_app(store, {"machine": {"token": "short", "workspaces": ["w"]}})
    with pytest.raises(ValueError, match="distinct"):
        build_app(store, {"a": {"token": "x" * 32, "workspaces": ["w"]}, "b": {"token": "x" * 32, "workspaces": ["w"]}})


def test_client_denies_insecure_hosts_and_lost_response(tmp_path):
    with pytest.raises(CoordinationError):
        CoordinationClient("http://remote.example", "a" * 32)

    def disconnected(request):
        raise httpx.ReadTimeout("network lost", request=request)

    client = CoordinationClient("https://coord.example", "a" * 32, transport=httpx.MockTransport(disconnected))
    with pytest.raises(CoordinationError, match="outcome is unknown"):
        client.reserve("w", "root", "task", "installation")


@pytest.mark.asyncio
async def test_host_does_not_offer_if_multica_stops_during_reservation(tmp_path):
    store = HandoffStore(tmp_path / "db")
    alive = {"value": True}

    def verify(task, issue):
        if not alive["value"]:
            raise ValueError("run stopped")
        return {"lineage": "root"}

    def reserve(lineage, task, instance):
        assert instance == store.instance_id()
        alive["value"] = False

    host = build_server(store, "host", verify, reserve)
    with pytest.raises(Exception, match="stopped"):
        await host.call_tool(
            "offer_task",
            {"lineage": "root", "task_id": "run", "issue_id": "issue", "workspace": str(tmp_path), "prompt": "work"},
        )
    assert store.inspect("root") is None


@pytest.mark.asyncio
async def test_host_cannot_renew_when_coordinator_is_unavailable(tmp_path):
    store = HandoffStore(tmp_path / "db")
    store.create("root", "run", {"workspace": str(tmp_path), "prompt": "work"})

    def verify(task, issue):
        return {"lineage": "root"}

    def disconnected(lineage, task, instance):
        raise CoordinationError("coordinator unavailable")

    host = build_server(store, "host", verifier=verify, check_reservation=disconnected)
    before = store.inspect("root")["lease_until"]
    with pytest.raises(Exception, match="coordinator unavailable"):
        await host.call_tool("renew_task", {"lineage": "root", "task_id": "run", "issue_id": "issue"})
    assert store.inspect("root")["lease_until"] == before


@pytest.mark.asyncio
async def test_two_machine_hosts_share_central_reservation_over_http(tmp_path):
    issue = "12345678-1234-1234-1234-123456789abc"
    token_a, token_b = "a" * 32, "b" * 32
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def git(*args):
        return (
            subprocess.check_output(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
                cwd=workspace,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )

    git("init")
    (workspace / "file.txt").write_text("initial", encoding="utf-8")
    git("add", "file.txt")
    git("commit", "-m", "initial")
    base = git("rev-parse", "HEAD")
    store = ReservationStore(tmp_path / "central.db")
    app = build_app(
        store,
        {"machine-a": {"token": token_a, "workspaces": ["w"]}, "machine-b": {"token": token_b, "workspaces": ["w"]}},
    )
    client = TestClient(app)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(
                [
                    {
                        "id": "task",
                        "agent_id": "agent",
                        "workspace_id": "w",
                        "issue_id": issue,
                        "status": "running",
                        "attribution": {},
                        "attempt": 1,
                    }
                ]
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            result = client.post(
                self.path,
                content=body,
                headers={"authorization": self.headers.get("authorization", ""), "content-type": "application/json"},
            )
            self.send_response(result.status_code)
            self.send_header("Content-Length", str(len(result.content)))
            self.end_headers()
            self.wfile.write(result.content)

        def log_message(self, format, *args):
            pass

    http_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{http_server.server_port}"

    async def connect(stack, token, path):
        env = {
            **os.environ,
            "MULTICA_SERVER_URL": url,
            "MULTICA_TOKEN": "mat_test",
            "MULTICA_TASK_ID": "task",
            "MULTICA_AGENT_ID": "agent",
            "MULTICA_WORKSPACE_ID": "w",
            "COOPERATION_COORDINATION_TOKEN": token,
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent_link.desktop_handoff",
                "--db",
                str(path),
                "--role",
                "host",
                "--multica-task",
                "--coordinator-url",
                url,
            ],
            env=env,
            cwd=str(tmp_path),
        )
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    try:
        async with AsyncExitStack() as stack:
            a = await connect(stack, token_a, tmp_path / "machine-a.db")
            b = await connect(stack, token_b, tmp_path / "machine-b.db")
            request = {
                "lineage": "task",
                "task_id": "task",
                "issue_id": issue,
                "workspace": str(workspace),
                "prompt": "work",
            }
            assert not (await a.call_tool("offer_task", request)).isError
            assert (await b.call_tool("offer_task", request)).isError
            inspected = await b.call_tool("inspect_coordinator", {"lineage": "task"})
            assert not inspected.isError
            assert (inspected.structuredContent or json.loads(inspected.content[0].text))["machine"] == "machine-a"
            assert store.inspect("w", "task")["machine"] == "machine-a"
            assert HandoffStore(tmp_path / "machine-b.db").inspect("task") is None
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "agent_link.desktop_handoff", "--db", str(tmp_path / "machine-a.db"), "--role", "desktop"],
                cwd=str(tmp_path),
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            desktop = await stack.enter_async_context(ClientSession(read, write))
            await desktop.initialize()
            claim_result = await desktop.call_tool("claim_task", {"lineage": "task"})
            assert not claim_result.isError
            claim = claim_result.structuredContent or json.loads(claim_result.content[0].text)
            (workspace / "file.txt").write_text("completed", encoding="utf-8")
            git("add", "file.txt")
            git("commit", "-m", "result")
            head = git("rev-parse", "HEAD")
            result = await desktop.call_tool(
                "submit_result",
                {"lineage": "task", "handle": claim["handle"], "summary": "completed", "evidence": [head]},
            )
            assert not result.isError
            evidence = await asyncio.wait_for(
                a.call_tool("verify_git_delivery", {"lineage": "task", "expected_commit": head, "base_commit": base}),
                timeout=20,
            )
            assert not evidence.isError
            verified = evidence.structuredContent or json.loads(evidence.content[0].text)
            assert verified["clean"] and verified["base_is_ancestor"] and verified["changed_paths"] == ["file.txt"]
    finally:
        http_server.shutdown()
        http_server.server_close()
        thread.join()
        client.close()
