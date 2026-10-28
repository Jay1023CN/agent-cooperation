"""Deterministic peer implementing the ACP subset used by contract tests."""

import json
import os
import sys

assert not any(name.upper().startswith("MULTICA_") for name in os.environ)


def emit(data):
    print(json.dumps({"jsonrpc": "2.0", **data}), flush=True)


prompt_id = None
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        emit({"id": request["id"], "result": {"protocolVersion": 1, "agentInfo": {"name": "fixture"}}})
    elif method == "session/new":
        emit({"id": request["id"], "result": {"sessionId": "fixture-session", "models": {"availableModels": []}}})
    elif method == "session/prompt":
        prompt_id = request["id"]
        emit(
            {
                "id": "permission-1",
                "method": "session/request_permission",
                "params": {
                    "sessionId": "fixture-session",
                    "toolCall": {"toolCallId": "edit-1", "title": "Edit fixture"},
                    "options": [{"optionId": "once", "name": "Allow once", "kind": "allow_once"}],
                },
            }
        )
    elif request.get("id") == "permission-1":
        selected = request["result"]["outcome"].get("optionId") == "once"
        emit(
            {
                "method": "session/update",
                "params": {
                    "sessionId": "fixture-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "ALLOWED" if selected else "CANCELLED"},
                    },
                },
            }
        )
        emit({"id": prompt_id, "result": {"stopReason": "end_turn"}})
