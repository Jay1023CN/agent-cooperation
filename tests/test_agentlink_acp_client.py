"""Real subprocess and workspace checks for ACP v1 client capabilities."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from agent_link.acp import ACP
from agent_link.acp_client import ClientResources
from agent_link.common import BridgeError


@pytest.fixture
def resources(tmp_path):
    def session(identifier):
        if identifier not in ("one", "two"):
            raise BridgeError("Unknown session")
        return {"workspace": str(tmp_path)}

    client = ClientResources(
        session,
        lambda *_: None,
        environment={**os.environ, "ARENA_MACHINE_TOKEN": "private", "multiCa_token": "private"},
    )
    yield client
    client.close()


@pytest.mark.parametrize("approve", [True, False])
def test_stdio_file_terminal_and_approval_roundtrip(tmp_path, approve):
    peer = Path(__file__).with_name("fake_agentlink_acp_client.py")
    adapter = ACP("qoder", {"command": [sys.executable, str(peer)]})
    try:
        session = adapter.create(str(tmp_path))["id"]
        adapter.send(session, "Exercise callbacks")
        waiting = adapter.wait(session, 5)
        assert waiting["status"] == "waiting_permission"
        assert (tmp_path / "written.txt").read_text() == "one\ntwo\n"
        assert not (tmp_path / "terminal.txt").exists()
        permission = waiting["permissions"][0]
        adapter.reply(permission["id"], "allow_once" if approve else "reject_once")
        done = adapter.wait(session, 5)
        if approve:
            assert done["status"] == "completed", done
            assert "Reader remained responsive" in done["text"]
            assert (tmp_path / "terminal.txt").read_text() == "checked"
        else:
            assert done["status"] in ("stopped", "empty_response")
            assert not (tmp_path / "terminal.txt").exists()
    finally:
        adapter.close()


def test_scope_limits_unknown_session_and_git_controls(resources, tmp_path):
    for params in (
        {"sessionId": "one", "path": str(tmp_path.parent / "outside")},
        {"sessionId": "one", "path": "relative.txt"},
        {"sessionId": "one", "path": str(tmp_path / ".git" / "config")},
        {"sessionId": "unknown", "path": str(tmp_path / "file")},
    ):
        with pytest.raises(BridgeError):
            resources.request("fs/write_text_file", {**params, "content": "blocked"})
    assert not (tmp_path / "file").exists()


def test_symlink_escape_is_rejected(resources, tmp_path):
    link = tmp_path / "escape"
    try:
        link.symlink_to(tmp_path.parent, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable for this OS account")
    with pytest.raises(BridgeError):
        resources.request("fs/write_text_file", {"sessionId": "one", "path": str(link / "outside"), "content": "no"})


def test_terminal_output_is_utf8_bounded_and_does_not_inherit_arena_tokens(resources, tmp_path):
    terminal = resources.request(
        "terminal/create",
        {
            "sessionId": "one",
            "command": sys.executable,
            "args": [
                "-c",
                "import os,sys; assert not any(k.upper().startswith(('ARENA_','MULTICA_')) for k in os.environ); sys.stdout.buffer.write(('中文'*40).encode())",
            ],
            "cwd": str(tmp_path),
            "outputByteLimit": 7,
        },
    )["terminalId"]
    result = resources.request("terminal/wait_for_exit", {"sessionId": "one", "terminalId": terminal})
    assert result["exitCode"] == 0
    output = resources.request("terminal/output", {"sessionId": "one", "terminalId": terminal})
    assert output["truncated"] is True
    assert 0 < len(output["output"].encode("utf-8")) <= 7
    assert "\ufffd" not in output["output"]
    with pytest.raises(BridgeError):
        resources.request("terminal/output", {"sessionId": "two", "terminalId": terminal})
    resources.request("terminal/release", {"sessionId": "one", "terminalId": terminal})
    with pytest.raises(BridgeError):
        resources.request("terminal/output", {"sessionId": "one", "terminalId": terminal})


def test_kill_unblocks_terminal_wait(resources):
    terminal = resources.request(
        "terminal/create",
        {"sessionId": "one", "command": sys.executable, "args": ["-c", "import time; time.sleep(30)"]},
    )["terminalId"]
    result = {}
    thread = threading.Thread(
        target=lambda: result.update(
            resources.request("terminal/wait_for_exit", {"sessionId": "one", "terminalId": terminal})
        )
    )
    thread.start()
    resources.request("terminal/kill", {"sessionId": "one", "terminalId": terminal})
    thread.join(5)
    assert not thread.is_alive()
    assert result.get("exitCode") != 0 or result.get("signal")


def test_close_cancels_pending_client_approval(tmp_path):
    peer = Path(__file__).with_name("fake_agentlink_acp_client.py")
    adapter = ACP("qodercn", {"command": [sys.executable, str(peer)]})
    session = adapter.create(str(tmp_path))["id"]
    adapter.send(session, "Await local approval")
    assert adapter.wait(session, 5)["status"] == "waiting_permission"
    started = time.monotonic()
    adapter.close()
    assert time.monotonic() - started < 5
    assert adapter.process.poll() is not None
    assert not (tmp_path / "terminal.txt").exists()


@pytest.mark.parametrize("cleanup_error", [None, "Process cleanup failed"])
def test_close_continues_after_peer_breaks_stdin_without_masking_cleanup_failure(monkeypatch, cleanup_error):
    import io

    adapter = ACP("qoder")
    stdin = Mock()
    stdin.close.side_effect = BrokenPipeError("Peer exited before the buffered cancellation was flushed")
    stdout, stderr = io.StringIO(), io.StringIO()
    adapter.process = SimpleNamespace(stdin=stdin, stdout=stdout, stderr=stderr)
    adapter.cleanup_error = cleanup_error
    stopped = Mock()
    monkeypatch.setattr("agent_link.acp.stop_tree", stopped)
    if cleanup_error:
        with pytest.raises(BridgeError, match=cleanup_error):
            adapter.close()
    else:
        adapter.close()
    stdin.close.assert_called_once()
    stopped.assert_called_once_with(adapter.process)
    assert stdout.closed and stderr.closed


def test_final_prompt_response_is_kept_when_agent_exits_immediately(tmp_path):
    peer = tmp_path / "exit_after_reply.py"
    peer.write_text(
        """import json,sys
def emit(value): print(json.dumps({"jsonrpc":"2.0",**value}),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    if request.get("method")=="initialize":
        emit({"id":request["id"],"result":{"protocolVersion":1}})
    elif request.get("method")=="session/new":
        emit({"id":request["id"],"result":{"sessionId":"one"}})
    elif request.get("method")=="session/prompt":
        emit({"method":"session/update","params":{"sessionId":"one","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"verified"}}}})
        emit({"id":request["id"],"result":{"stopReason":"end_turn"}})
        sys.exit(0)
""",
        encoding="utf-8",
    )
    adapter = ACP("qoder", {"command": [sys.executable, str(peer)]})
    try:
        session = adapter.create(str(tmp_path))["id"]
        adapter.send(session, "Reply then exit")
        result = adapter.wait(session, 5)
        assert result["status"] == "completed", result
        assert result["text"] == "verified"
    finally:
        adapter.close()


def test_execution_config_writes_request_client_approval(tmp_path):
    approvals = []
    client = ClientResources(lambda _: {"workspace": str(tmp_path)}, lambda *args: approvals.append(args))
    client.request("fs/write_text_file", {"sessionId": "one", "path": str(tmp_path / ".mcp.json"), "content": "{}"})
    assert approvals[0][1] == "write_control_file"
    assert (tmp_path / ".mcp.json").read_text() == "{}"


def test_protected_paths_are_casefolded_and_aliases_still_require_approval(tmp_path):
    approvals = []
    client = ClientResources(lambda _: {"workspace": str(tmp_path)}, lambda *args: approvals.append(args))
    with pytest.raises(BridgeError):
        client.request(
            "fs/write_text_file", {"sessionId": "one", "path": str(tmp_path / ".GIT" / "config"), "content": ""}
        )
    client.request(
        "fs/write_text_file", {"sessionId": "one", "path": str(tmp_path / ".QODER" / "settings.json"), "content": "{}"}
    )
    assert len(approvals) == 1


def test_initialize_failure_closes_process_and_cannot_masquerade_as_connected(tmp_path, monkeypatch):
    peer = Path(__file__).with_name("fake_agentlink_acp_client.py")
    adapter = ACP("qoder", {"command": [sys.executable, str(peer)]})

    def fail(*_args, **_kwargs):
        raise BridgeError("Initialize failed")

    monkeypatch.setattr(adapter, "call", fail)
    with pytest.raises(BridgeError, match="Initialize failed"):
        adapter.connect()
    assert adapter.process.poll() is not None
    with pytest.raises(BridgeError, match="fresh adapter"):
        adapter.connect()


def test_terminal_leader_exit_still_stops_background_descendant(resources, tmp_path):
    child = "import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path('child-ready').write_text('yes'); time.sleep(30)"
    parent = (
        "import subprocess,sys,time; from pathlib import Path; "
        f'p=subprocess.Popen([sys.executable,"-c",{child!r}]); '
        'Path("child-pid").write_text(str(p.pid)); '
        '\nwhile not Path("child-ready").exists(): time.sleep(0.01)\n'
    )
    terminal = resources.request(
        "terminal/create", {"sessionId": "one", "command": sys.executable, "args": ["-c", parent], "cwd": str(tmp_path)}
    )["terminalId"]
    state = resources.terminals[terminal]
    assert state["done"].wait(8), "A descendant retained the terminal pipe after the leader exited"
    pid = int((tmp_path / "child-pid").read_text())
    # The OS may reap it between existence and status checks, especially on
    # Windows. A running process must disappear (or become a zombie) promptly.
    deadline = time.monotonic() + 5
    while True:
        try:
            status = psutil.Process(pid).status()
        except psutil.NoSuchProcess:
            break
        if status == psutil.STATUS_ZOMBIE:
            break
        if time.monotonic() >= deadline:
            pytest.fail(f"Supervised background descendant is still alive (status {status})")
        time.sleep(0.05)


def test_late_approved_write_cannot_cross_prompt_turns(tmp_path):
    waiting, approval = threading.Event(), threading.Event()
    errors = []

    def authorize(*_):
        waiting.set()
        assert approval.wait(5)

    client = ClientResources(lambda _: {"workspace": str(tmp_path)}, authorize)
    client.begin("session")

    def write():
        try:
            client.request(
                "fs/write_text_file",
                {"sessionId": "session", "path": str(tmp_path / "AGENTS.md"), "content": "old turn"},
            )
        except BridgeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=write)
    thread.start()
    assert waiting.wait(5)
    client.close("session")
    client.begin("session")
    approval.set()
    thread.join(5)
    assert not thread.is_alive()
    assert errors and "earlier prompt" in errors[0]
    assert not (tmp_path / "AGENTS.md").exists()
    client.close()


def test_completed_prompt_reaps_terminals_before_reporting_success(tmp_path, monkeypatch):
    from agent_link import acp_client

    started = []
    spawn = acp_client.spawn_owned

    def capture(*args, **kwargs):
        process = spawn(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(acp_client, "spawn_owned", capture)
    peer = Path(__file__).with_name("fake_agentlink_acp_client.py")
    adapter = ACP("qoder", {"command": [sys.executable, str(peer)], "client_terminal_policy": "allow"})
    try:
        session = adapter.create(str(tmp_path))["id"]
        assert adapter.cancel(session)["cancel_requested"] is False
        assert adapter.read(session)["status"] == "idle"
        adapter.send(session, "Leave terminal running")
        assert adapter.wait(session, 5)["status"] == "completed"
        assert started and all(process.poll() is not None for process in started)
        assert not adapter.resources.terminals
        assert adapter.cancel(session)["cancel_requested"] is False
        assert adapter.read(session)["status"] == "completed"
        with pytest.raises(BridgeError):
            adapter.resources.request(
                "fs/write_text_file", {"sessionId": session, "path": str(tmp_path / "late.txt"), "content": "late"}
            )
        # A later intentional prompt gets a fresh resource generation.
        adapter.send(session, "Exercise callbacks again")
        assert adapter.wait(session, 5)["status"] == "completed"
        assert (tmp_path / "terminal.txt").read_text() == "checked"
    finally:
        adapter.close()


def test_cancel_before_prompt_submission_never_sends_it(tmp_path, monkeypatch):
    peer = Path(__file__).with_name("fake_agentlink_acp_client.py")
    adapter = ACP("qoder", {"command": [sys.executable, str(peer)]})
    blocked, release = threading.Event(), threading.Event()
    methods = []
    try:
        session = adapter.create(str(tmp_path))["id"]
        call, write = adapter.call, adapter._write

        def delayed_call(method, params, timeout=30):
            if method == "session/prompt":
                blocked.set()
                assert release.wait(5)
            return call(method, params, timeout)

        def record(message):
            methods.append(message.get("method"))
            return write(message)

        monkeypatch.setattr(adapter, "call", delayed_call)
        monkeypatch.setattr(adapter, "_write", record)
        adapter.send(session, "Do not execute this prompt")
        assert blocked.wait(5)
        assert adapter.cancel(session)["cancel_requested"]
        release.set()
        assert adapter.wait(session, 5)["status"] == "stopped"
        assert "session/cancel" in methods
        assert "session/prompt" not in methods
        assert not (tmp_path / "written.txt").exists()
    finally:
        release.set()
        adapter.close()


def test_cancel_waits_for_an_in_progress_submission(tmp_path, monkeypatch):
    peer = Path(__file__).with_name("fake_agentlink_acp_client.py")
    adapter = ACP("qoder", {"command": [sys.executable, str(peer)]})
    blocked, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    methods = []
    try:
        session = adapter.create(str(tmp_path))["id"]
        write = adapter._write

        def delayed_write(message):
            if message.get("method") == "session/prompt":
                blocked.set()
                assert release.wait(5)
            methods.append(message.get("method"))
            return write(message)

        monkeypatch.setattr(adapter, "_write", delayed_write)
        adapter.send(session, "Submit before cancelling")
        assert blocked.wait(5)
        thread = threading.Thread(target=lambda: (adapter.cancel(session), cancelled.set()))
        thread.start()
        assert not cancelled.wait(0.1)
        release.set()
        assert cancelled.wait(5)
        thread.join(5)
        assert methods.index("session/prompt") < methods.index("session/cancel")
        assert adapter.wait(session, 5)["status"] == "stopped"
    finally:
        release.set()
        adapter.close()


def test_owned_process_rejects_non_pid_test_double(monkeypatch):
    from unittest.mock import MagicMock

    from agent_link import process_owner

    fake = MagicMock()
    monkeypatch.setattr(process_owner.subprocess, "Popen", lambda *_args, **_kwargs: fake)
    with pytest.raises(RuntimeError, match="independent process group"):
        process_owner.spawn_owned(["unused"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_owned_process_verifies_integer_pid_belongs_to_its_own_group(monkeypatch):
    from unittest.mock import MagicMock

    from agent_link import process_owner

    fake = MagicMock()
    fake.pid = os.getpid()
    fake.poll.return_value = None
    calls = []
    monkeypatch.setattr(process_owner.subprocess, "Popen", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(process_owner.os, "getpgid", lambda _: fake.pid + 1)
    monkeypatch.setattr(process_owner.os, "getpgrp", lambda: fake.pid + 2)
    monkeypatch.setattr(process_owner.os, "killpg", lambda *args: calls.append(args))
    with pytest.raises(RuntimeError, match="own process group"):
        process_owner.spawn_owned(["unused"])
    assert not calls


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group supervision")
def test_reaped_supervisor_is_not_signalled_as_a_reused_group(monkeypatch):
    from types import SimpleNamespace

    from agent_link import process_owner

    calls = []
    process = SimpleNamespace(
        pid=987654321,
        _arena_stop_lock=threading.Lock(),
        _arena_stopped=False,
        _arena_supervisor=SimpleNamespace(poll=lambda: 0),
    )
    monkeypatch.setattr(process_owner.os, "killpg", lambda *args: calls.append(args))
    with pytest.raises(RuntimeError, match="outcome is unknown"):
        process_owner.stop_owned(process)
    assert not process._arena_stopped and not calls


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group permissions")
def test_live_process_permission_failure_is_not_reported_as_cleaned(monkeypatch):
    from types import SimpleNamespace

    from agent_link import process_owner

    process = SimpleNamespace(
        pid=987654321,
        _arena_stop_lock=threading.Lock(),
        _arena_stopped=False,
        _arena_supervisor=SimpleNamespace(poll=lambda: None),
    )

    def denied(*_):
        raise PermissionError("cannot signal live child")

    monkeypatch.setattr(process_owner.os, "killpg", denied)
    with pytest.raises(PermissionError):
        process_owner.stop_owned(process)
    assert not process._arena_stopped


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group supervision")
def test_supervisor_keeps_group_identity_after_child_exits():
    from agent_link.process_owner import spawn_owned, stop_owned

    process = spawn_owned(
        [
            sys.executable,
            "-c",
            "import sys; print(sys.stdin.readline().strip()); print('err',file=sys.stderr); sys.exit(7)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.stdin.write(b"hello\n")
        process.stdin.close()
        assert process.stdout.read().strip() == b"hello"
        assert process.stderr.read().strip() == b"err"
        assert process.wait(timeout=5) == 7
        assert process._arena_supervisor.poll() is None
        assert os.getpgid(process.pid) == process.pid
    finally:
        stop_owned(process)
    assert process._arena_stopped and process._arena_supervisor.poll() is not None


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group supervision")
def test_supervisor_restores_the_requested_loader_environment():
    from agent_link.process_owner import spawn_owned, stop_owned

    env = {**os.environ, "LD_LIBRARY_PATH": "/__arena_test_loader__", "_PAL_SUPERVISOR_TEST": "private"}
    process = spawn_owned(
        [
            sys.executable,
            "-c",
            "import os; assert os.environ['LD_LIBRARY_PATH'] == '/__arena_test_loader__'; "
            "assert not any(k.startswith('_PAL_SUPERVISOR_') for k in os.environ)",
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.wait(timeout=5) == 0, process.stderr.read().decode(errors="replace")
    finally:
        stop_owned(process)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group supervision")
def test_unexpected_supervisor_exit_cannot_report_success(monkeypatch):
    from types import SimpleNamespace

    from agent_link import process_owner

    control_read, control_write = os.pipe()
    process = SimpleNamespace(
        pid=987654321,
        _arena_stop_lock=threading.Lock(),
        _arena_stopped=False,
        _arena_supervisor=SimpleNamespace(poll=lambda: None, wait=lambda **_: 0),
        _arena_control_fd=control_write,
        _arena_status_thread=SimpleNamespace(join=lambda **_: None),
        _arena_status_known=False,
        wait=lambda **_: -1,
    )
    monkeypatch.setattr(process_owner.os, "killpg", lambda *_: None)
    monkeypatch.setattr(process_owner.time, "sleep", lambda *_: None)
    try:
        with pytest.raises(RuntimeError, match="status was lost"):
            process_owner.stop_owned(process)
        assert not process._arena_stopped
    finally:
        os.close(control_read)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group supervision")
def test_supervisor_stops_child_after_owner_process_disappears(tmp_path):
    root = Path(__file__).resolve().parents[1]
    child = (
        "import os,time; from pathlib import Path; " "Path('child.pid').write_text(str(os.getpid())); time.sleep(30)"
    )
    parent = (
        "import os,subprocess,sys,time; from pathlib import Path; "
        "from agent_link.process_owner import spawn_owned; "
        f"p=spawn_owned([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "Path('supervisor.pid').write_text(str(p.pid)); "
        "\nwhile not Path('child.pid').exists(): time.sleep(0.01)\n"
        "os._exit(0)\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root), os.environ.get("PYTHONPATH", "")])}
    subprocess.run([sys.executable, "-c", parent], cwd=tmp_path, env=env, check=True, timeout=10)
    child_pid = int((tmp_path / "child.pid").read_text())
    supervisor_pid = int((tmp_path / "supervisor.pid").read_text())
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        alive = []
        for pid in (child_pid, supervisor_pid):
            try:
                if psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                    alive.append(pid)
            except psutil.NoSuchProcess:
                pass
        if not alive:
            break
        time.sleep(0.05)
    if alive:
        try:
            if os.getpgid(child_pid) == supervisor_pid:
                os.killpg(supervisor_pid, 9)
        except ProcessLookupError:
            pass
    assert not alive, f"Process group survived owner exit: {alive}"


def test_windows_batch_entrypoint_is_not_treated_as_literal_argv(resources, monkeypatch):
    from agent_link import acp_client

    monkeypatch.setattr(acp_client.sys, "platform", "win32")
    monkeypatch.setattr(acp_client.shutil, "which", lambda _: "C:/node/npm.cmd")
    with pytest.raises(BridgeError, match="batch entrypoints"):
        resources.request("terminal/create", {"sessionId": "one", "command": "npm", "args": ["a&b"]})


def test_terminal_cannot_inject_arena_variables_in_other_case(resources):
    with pytest.raises(BridgeError, match="reserved"):
        resources.request(
            "terminal/create",
            {"sessionId": "one", "command": sys.executable, "env": [{"name": "arena_run_token", "value": "bad"}]},
        )


def test_terminal_cannot_inject_multica_task_identity(resources):
    with pytest.raises(BridgeError, match="reserved"):
        resources.request(
            "terminal/create",
            {"sessionId": "one", "command": sys.executable, "env": [{"name": "multica_token", "value": "bad"}]},
        )
