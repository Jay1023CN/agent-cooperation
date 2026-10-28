"""A real stdio peer exercises the official ACP client callback sequence."""

import json
import sys
from pathlib import Path


def emit(value):
    print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)


workspace = None
prompt_id = None
terminal_id = None
leave_running = False
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        assert request["params"]["clientCapabilities"] == {
            "fs": {"readTextFile": True, "writeTextFile": True},
            "terminal": True,
        }
        emit({"id": request["id"], "result": {"protocolVersion": 1, "agentInfo": {"name": "callback-fixture"}}})
    elif method == "session/new":
        workspace = Path(request["params"]["cwd"])
        emit({"id": request["id"], "result": {"sessionId": "callback-session"}})
    elif method == "session/prompt":
        prompt_id = request["id"]
        leave_running = "Leave terminal running" in request["params"]["prompt"][0]["text"]
        emit(
            {
                "id": "write",
                "method": "fs/write_text_file",
                "params": {
                    "sessionId": "callback-session",
                    "path": str(workspace / "written.txt"),
                    "content": "one\ntwo\n",
                },
            }
        )
    elif method == "session/cancel":
        if prompt_id is not None:
            emit({"id": prompt_id, "result": {"stopReason": "cancelled"}})
            prompt_id = None
    elif request.get("id") == "write":
        assert request.get("result") == {}
        emit(
            {
                "id": "read",
                "method": "fs/read_text_file",
                "params": {
                    "sessionId": "callback-session",
                    "path": str(workspace / "written.txt"),
                    "line": 2,
                    "limit": 1,
                },
            }
        )
    elif request.get("id") == "read":
        assert request["result"]["content"] == "two\n"
        emit(
            {
                "id": "create",
                "method": "terminal/create",
                "params": {
                    "sessionId": "callback-session",
                    "command": sys.executable,
                    "args": [
                        "-c",
                        (
                            "import time; time.sleep(30)"
                            if leave_running
                            else "from pathlib import Path; import time; time.sleep(0.4); Path('terminal.txt').write_text('checked'); print('terminal completed')"
                        ),
                    ],
                    "cwd": str(workspace),
                    "outputByteLimit": 1024,
                },
            }
        )
    elif request.get("id") == "create":
        if "error" in request:
            emit({"id": prompt_id, "result": {"stopReason": "refusal"}})
            prompt_id = None
            continue
        terminal_id = request["result"]["terminalId"]
        if leave_running:
            emit(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "callback-session",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "Left a terminal running."},
                        },
                    },
                }
            )
            emit({"id": prompt_id, "result": {"stopReason": "end_turn"}})
            prompt_id = None
            continue
        emit(
            {
                "id": "wait",
                "method": "terminal/wait_for_exit",
                "params": {"sessionId": "callback-session", "terminalId": terminal_id},
            }
        )
        # This must be serviced while the earlier terminal wait remains outstanding.
        emit(
            {
                "id": "parallel-read",
                "method": "fs/read_text_file",
                "params": {"sessionId": "callback-session", "path": str(workspace / "written.txt")},
            }
        )
    elif request.get("id") == "parallel-read":
        assert request["result"]["content"] == "one\ntwo\n"
        emit(
            {
                "method": "session/update",
                "params": {
                    "sessionId": "callback-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "Reader remained responsive. "},
                    },
                },
            }
        )
    elif request.get("id") == "wait":
        assert request["result"]["exitCode"] == 0
        emit(
            {
                "id": "output",
                "method": "terminal/output",
                "params": {"sessionId": "callback-session", "terminalId": terminal_id},
            }
        )
    elif request.get("id") == "output":
        assert "terminal completed" in request["result"]["output"]
        assert request["result"]["exitStatus"]["exitCode"] == 0
        emit(
            {
                "id": "release",
                "method": "terminal/release",
                "params": {"sessionId": "callback-session", "terminalId": terminal_id},
            }
        )
    elif request.get("id") == "release":
        emit(
            {
                "method": "session/update",
                "params": {
                    "sessionId": "callback-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "Files and terminal verified."},
                    },
                },
            }
        )
        emit({"id": prompt_id, "result": {"stopReason": "end_turn"}})
        prompt_id = None
