"""DSH-shaped ACP peer: profiles and config options, with no model or network calls."""

import json
import sys
from pathlib import Path

assert sys.argv[-2:] == ["--profile", "cooperation"]
transcript = Path(sys.argv[1])
prompt_id = None


def emit(data):
    print(json.dumps({"jsonrpc": "2.0", **data}), flush=True)


def options(value="configured-route"):
    return [
        {
            "id": "model",
            "category": "model",
            "type": "select",
            "currentValue": value,
            "options": [{"value": "configured-route", "name": "Configured provider/model"}],
        }
    ]


for line in sys.stdin:
    request = json.loads(line)
    with transcript.open("a", encoding="utf-8") as log:
        log.write(json.dumps(request) + "\n")
    method = request.get("method")
    params = request.get("params", {})
    if method == "initialize":
        emit({"id": request["id"], "result": {"protocolVersion": 1, "agentInfo": {"name": "dsh-fixture"}}})
    elif method == "session/new":
        assert Path(params["cwd"]).is_absolute()
        emit({"id": request["id"], "result": {"sessionId": "dsh-task", "configOptions": options()}})
    elif method == "session/set_config_option":
        assert params == {"sessionId": "dsh-task", "configId": "model", "value": "configured-route"}
        emit({"id": request["id"], "result": {"configOptions": options(params["value"])}})
    elif method == "session/prompt":
        prompt_id = request["id"]
        emit(
            {
                "id": "approval",
                "method": "session/request_permission",
                "params": {
                    "sessionId": "dsh-task",
                    "toolCall": {"toolCallId": "edit", "title": "Write task artifact"},
                    "options": [{"optionId": "once", "name": "Allow once", "kind": "allow_once"}],
                },
            }
        )
    elif method == "session/cancel":
        emit({"id": prompt_id, "result": {"stopReason": "cancelled"}})
    elif request.get("id") == "approval":
        if request["result"]["outcome"].get("optionId") == "once":
            emit(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "dsh-task",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "DSH task complete"},
                        },
                    },
                }
            )
            emit({"id": prompt_id, "result": {"stopReason": "end_turn"}})
    else:
        emit({"id": request.get("id"), "error": {"code": -32601, "message": "Unsupported DSH method"}})
