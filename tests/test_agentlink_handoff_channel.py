from __future__ import annotations

import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_link.handoff_channel import HandoffChannel
from agent_link.handoff_store import HandoffError, HandoffStore


def offer(store, name="one", recipient="claude-ui"):
    store.create(name, name, {"prompt": f"Implement {name}", "recipient": recipient})


def test_atomic_targeted_claim_and_recovery_survive_restart(tmp_path):
    path = tmp_path / "handoff.db"
    store = HandoffStore(path)
    offer(store)
    assert store.claim_next("codex", "request") is None
    with pytest.raises(HandoffError, match="Targeted"):
        store.claim("one")
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda _: HandoffStore(path).claim_next("claude-ui", "request"), range(6)))
    assert len({r["handle"] for r in receipts}) == 1
    assert sum(not r["recovered"] for r in receipts) == 1
    assert store.inspect("one")["consumer_id"] == "claude-ui"
    with pytest.raises(HandoffError, match="already owns"):
        store.claim_next("claude-ui", "different")
    receipt = receipts[0]
    store.submit("one", receipt["handle"], {"summary": "candidate"})
    offer(store, "two")
    recovered = HandoffStore(path).claim_next("claude-ui", "request")
    assert recovered["state"] == "submitted"
    assert "handle" not in recovered and "payload" not in recovered
    assert store.inspect("two")["state"] == "pending"


def test_expired_claim_cannot_quietly_move_consumer_to_another_task(tmp_path):
    store = HandoffStore(tmp_path / "db")
    offer(store)
    store.claim_next("claude-ui", "first")
    store.expire("one")
    offer(store, "two")
    with pytest.raises(HandoffError, match="already owns"):
        store.claim_next("claude-ui", "second")
    assert store.claim_next("claude-ui", "first")["state"] == "expired"
    assert store.inspect("two")["state"] == "pending"


def test_messages_have_role_bound_handles_and_stable_cursors(tmp_path):
    store = HandoffStore(tmp_path / "db")
    offer(store)
    claim = store.claim_next("claude-ui", "request")
    handle = claim["handle"]
    host = store.post_message("host", "one", "clarify", "只改前端", "directive")
    assert store.post_message("host", "one", "clarify", "只改前端", "directive") == host
    with pytest.raises(HandoffError, match="different content"):
        store.post_message("host", "one", "clarify", "changed", "directive")
    with pytest.raises(HandoffError, match="Invalid handoff"):
        store.read_messages("desktop", "one", handle="wrong")
    with pytest.raises(HandoffError, match="cannot send"):
        store.post_message("desktop", "one", "wrong-role", "no", "directive", handle)
    progress = store.post_message("desktop", "one", "coding", "正在修改界面", "progress", handle)
    desktop = store.read_messages("desktop", "one", limit=1, handle=handle)
    assert [m["id"] for m in desktop["messages"]] == [host["id"]]
    assert store.read_messages("host", "one")["messages"] == [progress]
    second = store.post_message("host", "one", "clarify2", "不执行测试", "note")
    assert store.read_messages("desktop", "one", limit=1, handle=handle)["has_more"] is True
    page = store.read_messages("desktop", "one", desktop["next_after"], handle=handle)
    assert page["messages"] == [second]
    store.cancel("one")
    assert store.read_messages("desktop", "one", handle=handle)["state"] == "cancelled"
    with pytest.raises(HandoffError, match="not accepting"):
        store.post_message("desktop", "one", "late", "late", "progress", handle)
    assert store.post_message("desktop", "one", "coding", "正在修改界面", "progress", handle) == progress


@pytest.mark.parametrize("value", [-1, 26, True, float("inf"), float("nan")])
@pytest.mark.asyncio
async def test_invalid_wait_never_claims_or_registers_listener(tmp_path, value):
    store = HandoffStore(tmp_path / "db")
    offer(store)
    with pytest.raises(HandoffError, match="wait_seconds"):
        await HandoffChannel(store).next_task("claude-ui", "request", value)
    assert store.inspect("one")["state"] == "pending"
    assert store.listeners() == []


@pytest.mark.asyncio
async def test_wait_is_visible_and_disconnect_does_not_claim_later(tmp_path):
    store = HandoffStore(tmp_path / "db")
    channel = HandoffChannel(store)
    waiting = asyncio.create_task(channel.next_task("claude-ui", "request", 10))
    for _ in range(100):
        if store.listeners():
            break
        await asyncio.sleep(0.01)
    assert store.listeners()[0]["consumer_id"] == "claude-ui"
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert store.listeners() == []
    offer(store)
    await asyncio.sleep(0.25)
    assert store.inspect("one")["state"] == "pending"


@pytest.mark.asyncio
async def test_idle_cancellation_and_complete_and_wait_recovery(tmp_path):
    store = HandoffStore(tmp_path / "db")
    channel = HandoffChannel(store)
    assert (await channel.next_task("claude-ui", "empty", 0))["state"] == "idle"
    offer(store)
    store.post_message("host", "one", "before-claim", "先读这条补充要求", "directive")
    first = await channel.next_task("claude-ui", "first", 0)
    assert first["messages"][0]["text"] == "先读这条补充要求"
    offer(store, "two")
    with pytest.raises(HandoffError, match="Consumer does not own"):
        await channel.complete_and_wait("one", first["handle"], "done", ["file"], "other", "next", 0)
    assert store.inspect("one")["state"] == "claimed"
    completed = await channel.complete_and_wait("one", first["handle"], "done", ["file"], "claude-ui", "second", 0)
    assert completed["next"]["lineage"] == "two"
    repeated = await channel.complete_and_wait("one", first["handle"], "done", ["file"], "claude-ui", "second", 0)
    assert repeated["next"]["handle"] == completed["next"]["handle"]
    update = asyncio.create_task(
        channel.wait_for_update("desktop", "two", handle=completed["next"]["handle"], seconds=2)
    )
    await asyncio.sleep(0.05)
    store.cancel("two")
    assert (await update)["state"] == "cancelled"


def test_message_and_payload_bounds(tmp_path):
    store = HandoffStore(tmp_path / "db")
    with pytest.raises(HandoffError, match="256 KB"):
        store.create("too-big", "too-big", {"prompt": "x" * 256_000})
    offer(store)
    for value in ("", "中文" * 3000):
        with pytest.raises(HandoffError, match="8192"):
            store.post_message("host", "one", "message", value)
    for after in (-1, 2**63, True):
        with pytest.raises(HandoffError, match="cursor"):
            store.read_messages("host", "one", after)


def test_wait_identity_conflicts_and_message_capacity_are_bounded(tmp_path):
    store = HandoffStore(tmp_path / "db")
    store.begin_wait("claude-ui", "first", 10)
    with pytest.raises(HandoffError, match="waiting request"):
        store.begin_wait("claude-ui", "second", 10)
    store.end_wait("claude-ui", "second")
    assert store.listeners()
    store.end_wait("claude-ui", "first")
    assert store.listeners() == []
    offer(store)
    for index in range(200):
        store.post_message("host", "one", f"message-{index}", "brief")
    with pytest.raises(HandoffError, match="message limit"):
        store.post_message("host", "one", "overflow", "brief")
    assert store.post_message("host", "one", "message-0", "brief")["message_id"] == "message-0"


def test_legacy_recipient_and_messages_survive_upgrade(tmp_path):
    import sqlite3
    import time

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE handoffs (lineage TEXT PRIMARY KEY, task_id TEXT UNIQUE, payload TEXT, "
            "state TEXT, handle TEXT, result TEXT, updated REAL, lease_until REAL, workspace_key TEXT)"
        )
        db.execute(
            "INSERT INTO handoffs VALUES ('one','one',?,'pending',NULL,NULL,?,?,NULL)",
            (json.dumps({"recipient": "claude-ui", "prompt": "保留原记录"}), time.time(), time.time() + 60),
        )
    store = HandoffStore(path)
    assert store.claim_next("codex", "other") is None
    receipt = store.claim_next("claude-ui", "first")
    message = store.post_message("host", "one", "note", "保持已有数据")
    reopened = HandoffStore(path)
    assert reopened.claim_next("claude-ui", "first")["handle"] == receipt["handle"]
    assert reopened.read_messages("desktop", "one", handle=receipt["handle"])["messages"] == [message]


@pytest.mark.asyncio
async def test_two_real_mcp_processes_continue_without_desktop_control(tmp_path):
    """Exercise real stdio MCP transports; no model, CLI agent or UI automation is involved."""
    database = tmp_path / "handoff.db"
    async with AsyncExitStack() as stack:

        async def connect(role):
            params = StdioServerParameters(
                command=sys.executable,
                args=["-B", "-X", "utf8", "-m", "agent_link.desktop_handoff", "--db", str(database), "--role", role],
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            streams = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(*streams))
            await session.initialize()
            return session

        host, desktop = await connect("host"), await connect("desktop")
        prompts = await desktop.list_prompts()
        assert "cooperate" in {prompt.name for prompt in prompts.prompts}
        bootstrap = await desktop.get_prompt("cooperate", {"consumer_id": "claude-ui"})
        assert "complete_and_wait" in bootstrap.messages[0].content.text

        async def call(client, name, **params):
            result = await client.call_tool(name, params)
            assert not result.isError, result.content
            return result.structuredContent or json.loads(result.content[0].text)

        assert (await call(desktop, "channel_capabilities"))["application_wakeup"] is False
        waiting = asyncio.create_task(
            call(desktop, "next_task", consumer_id="claude-ui", request_id="first", wait_seconds=3)
        )
        for _ in range(50):
            if (await call(host, "list_listeners"))["listeners"]:
                break
            await asyncio.sleep(0.02)
        assert not waiting.done()
        folder = tmp_path / "first"
        folder.mkdir()
        await call(
            host,
            "offer_task",
            lineage="one",
            task_id="one",
            workspace=str(folder),
            prompt="Implement UI",
            recipient="claude-ui",
        )
        first = await waiting
        assert first["state"] == "claimed"
        await call(
            desktop,
            "report_progress",
            lineage="one",
            handle=first["handle"],
            message_id="progress",
            summary="正在修改界面",
        )
        progress = await call(host, "wait_for_update", lineage="one", known_state="claimed", wait_seconds=0)
        assert progress["messages"][0]["kind"] == "progress"
        await call(host, "post_message", lineage="one", message_id="scope", text="不要改后端", kind="directive")
        instruction = await call(desktop, "wait_for_update", lineage="one", handle=first["handle"], wait_seconds=0)
        assert instruction["messages"][0]["text"] == "不要改后端"
        continuation = asyncio.create_task(
            call(
                desktop,
                "complete_and_wait",
                lineage="one",
                handle=first["handle"],
                summary="UI candidate",
                evidence=["frontend/src/views/connection.js"],
                consumer_id="claude-ui",
                request_id="second",
                wait_seconds=3,
            )
        )
        result = await call(host, "wait_for_update", lineage="one", after=progress["next_after"], wait_seconds=3)
        assert result["state"] == "submitted"
        assert result["result"]["summary"] == "UI candidate"
        second_folder = tmp_path / "second"
        second_folder.mkdir()
        await call(
            host,
            "offer_task",
            lineage="two",
            task_id="two",
            workspace=str(second_folder),
            prompt="Next UI task",
            recipient="claude-ui",
        )
        continued = await continuation
        assert continued["next"]["lineage"] == "two"
        await call(host, "cancel_task", lineage="two")
        assert (
            await call(desktop, "wait_for_update", lineage="two", handle=continued["next"]["handle"], wait_seconds=0)
        )["state"] == "cancelled"
