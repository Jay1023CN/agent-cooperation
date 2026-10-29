import hashlib
import json
import os
import socket
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

from agent_link import claude_peer
from agent_link.claude_peer import ClaudePeer
from agent_link.desktop_handoff import build_server
from agent_link.handoff_client import call
from agent_link.handoff_store import HandoffError, HandoffStore


@pytest.fixture
def peer_fixture(tmp_path, monkeypatch):
    registry = tmp_path / "sessions"
    registry.mkdir()
    with tempfile.TemporaryDirectory(prefix="cc-peer-") as folder:
        connection = None
        endpoint = r"\\.\pipe\LOCAL\cc-msg-" + "a" * 32
        if os.name != "nt":
            endpoint = str(Path(folder) / "inbox.sock")
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.bind(endpoint)
            connection.listen()
        record = {
            "pid": os.getpid(),
            "sessionId": "test-session",
            "cwd": str(tmp_path),
            "procStart": "start",
            "peerProtocol": 1,
            "messagingSocketPath": endpoint,
        }
        (registry / f"{os.getpid()}.json").write_text(json.dumps(record))
        canonical = os.path.normcase(endpoint) if os.name == "nt" else str(Path(endpoint).resolve())
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        key_path = registry / f"{os.getpid()}.{digest}.key"
        key = {"peerToken": "a" * 32, "procStartFt" if os.name == "nt" else "procStart": "start"}
        key_path.write_text(json.dumps(key))
        key_path.chmod(0o600)
        monkeypatch.setattr(claude_peer, "_process_start", lambda pid: "start")
        yield ClaudePeer(registry), record, key_path, connection
        if connection:
            connection.close()


def test_peer_transport_uses_only_peer_key_and_never_asserts_permission_class(peer_fixture, monkeypatch, tmp_path):
    peer, record, key_path, listener = peer_fixture
    writes = []
    monkeypatch.setattr(claude_peer, "_write_pipe", lambda path, data: writes.append(data))
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "MUST-NOT-BE-USED")
    assert peer.list_sessions()[0]["sessionId"] == "test-session"
    result = peer.send("test-session", str(tmp_path), "直接修改前端", "notification-1")
    if listener:
        listener.settimeout(2)
        with listener.accept()[0] as stream:
            writes.append(stream.recv(16384))
    frames = [json.loads(line) for line in writes[0].splitlines()]
    assert frames[0] == {"type": "auth", "token": "a" * 32}
    assert frames[1]["message"]["content"] == "直接修改前端"
    assert set(frames[1]) == {"type", "msgV", "msg_id", "message"}
    assert result["state"] == "transport_written" and result["delivered"] is False
    assert "MUST-NOT" not in str(frames)


def test_wrong_workspace_stale_process_bad_key_and_oversized_messages_do_not_send(peer_fixture, monkeypatch, tmp_path):
    peer, record, key_path, _ = peer_fixture
    writes = []
    monkeypatch.setattr(claude_peer, "_write_pipe", lambda *args: writes.append(args))
    for session, cwd, text in [
        ("unknown", str(tmp_path), "task"),
        ("test-session", str(tmp_path / "other"), "task"),
        ("test-session", str(tmp_path), "中" * 3000),
    ]:
        with pytest.raises(HandoffError):
            peer.send(session, cwd, text, "id")
    key_path.write_text(json.dumps({"peerToken": "b" * 32, "procStartFt": "wrong", "procStart": "wrong"}))
    with pytest.raises(HandoffError, match="different process"):
        peer.send("test-session", str(tmp_path), "task", "id")
    monkeypatch.setattr(claude_peer, "_process_start", lambda pid: "different")
    assert peer.list_sessions() == []
    assert not writes


def test_registry_rejects_oversized_record(peer_fixture):
    peer, record, key_path, _ = peer_fixture
    registry_file = key_path.parent / f"{record['pid']}.json"
    registry_file.write_text("x" * 65537)
    assert peer.list_sessions() == []


def test_registry_rejects_symlink(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks requires platform permission")
    with pytest.raises(HandoffError, match="registry file"):
        claude_peer._json_file(link)


@pytest.mark.asyncio
async def test_host_notification_is_opt_in_and_uncertain_delivery_is_not_retried(tmp_path):
    store = HandoffStore(tmp_path / "handoff.sqlite")
    store.create("task", "task", {"workspace": str(tmp_path), "prompt": "Build UI", "recipient": "claude-ui"})

    class Peer:
        calls = 0

        def list_sessions(self):
            return []

        def send(self, *args):
            self.calls += 1
            assert "handoff_client" in args[2]
            raise HandoffError("uncertain test failure")

    peer = Peer()
    default = build_server(store, "host")
    assert "notify_claude_task" not in {tool.name for tool in await default.list_tools()}
    server = build_server(store, "host", claude_peer=peer)
    params = {"lineage": "task", "session_id": "test-session", "expected_cwd": str(tmp_path)}
    with pytest.raises(Exception, match="uncertain test failure"):
        await server.call_tool("notify_claude_task", params)
    restarted = build_server(HandoffStore(tmp_path / "handoff.sqlite"), "host", claude_peer=peer)
    result = await restarted.call_tool("notify_claude_task", params)
    assert peer.calls == 1
    assert "uncertain" in str(result) and "recovered" in str(result)
    assert store.inspect("task")["notifications"] == [{"session_id": "test-session", "state": "uncertain"}]
    store.cancel("task")
    with pytest.raises(Exception, match="pending"):
        await server.call_tool("notify_claude_task", params)


@pytest.mark.asyncio
async def test_successful_notification_still_requires_a_claim_receipt(tmp_path):
    store = HandoffStore(tmp_path / "handoff.sqlite")
    store.create("task", "task", {"workspace": str(tmp_path), "prompt": "Build UI", "recipient": "claude-ui"})

    class Peer:
        calls = 0

        def send(self, session_id, cwd, text, message_id):
            self.calls += 1
            return {"state": "transport_written", "delivered": False}

    peer = Peer()
    server = build_server(store, "host", claude_peer=peer)
    params = {"lineage": "task", "session_id": "test-session", "expected_cwd": str(tmp_path)}
    await server.call_tool("notify_claude_task", params)
    await server.call_tool("notify_claude_task", params)
    assert peer.calls == 1
    assert store.inspect("task")["state"] == "pending"
    assert store.inspect("task")["consumer_id"] is None
    assert store.inspect("task")["notifications"] == [{"session_id": "test-session", "state": "transport_written"}]
    receipt = await call(
        str(tmp_path / "handoff.sqlite"),
        "next_task",
        {"consumer_id": "claude-ui", "request_id": "one", "wait_seconds": 0},
    )
    assert receipt["state"] == "claimed"
    assert store.inspect("task")["consumer_id"] == "claude-ui"


@pytest.mark.asyncio
async def test_client_calls_a_real_stdio_server_without_starting_a_model(tmp_path):
    result = await call(
        str(tmp_path / "handoff.sqlite"), "next_task", {"consumer_id": "ui", "request_id": "one", "wait_seconds": 0}
    )
    assert result["state"] == "idle"


def test_current_process_identity_is_available():
    assert claude_peer._process_start(os.getpid())


@pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe transport")
def test_windows_pipe_writes_complete_auth_and_message_without_desktop(tmp_path):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR] + [wintypes.DWORD] * 6 + [ctypes.c_void_p]
    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    endpoint = r"\\.\pipe\LOCAL\cc-msg-" + uuid.uuid4().hex
    handle = kernel.CreateNamedPipeW(endpoint, 1, 0, 1, 16384, 16384, 2000, None)
    assert handle != ctypes.c_void_p(-1).value
    received = []

    def read():
        try:
            connected = kernel.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == 535
            if connected:
                buffer = ctypes.create_string_buffer(16384)
                length = wintypes.DWORD()
                if kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(length), None):
                    received.append(buffer.raw[: length.value])
        finally:
            kernel.DisconnectNamedPipe(handle)
            kernel.CloseHandle(handle)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    payload = b'{"type":"auth","token":"test"}\n{"type":"user"}\n'
    claude_peer._write_pipe(endpoint, payload)
    thread.join(3)
    assert not thread.is_alive()
    assert received == [payload]
