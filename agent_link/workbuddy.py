from __future__ import annotations

import os
import time
from urllib.parse import quote

from .common import BridgeError, JsonHTTP


class WorkBuddy:
    kind = "official-local-assistant-api"
    session_id = "local-assistant"

    def __init__(self, config=None):
        self.config = config or {}
        self.http = None
        self.last_message_id = None

    def connect(self):
        token_env = self.config.get("token_env", "WORKBUDDY_ACCESS_TOKEN")
        token = os.getenv(token_env)
        if not token:
            raise BridgeError(
                f"WorkBuddy OAuth access token missing. Complete official OAuth and set {token_env}; no desktop credentials are extracted."
            )
        self.http = JsonHTTP(
            self.config.get("url", "https://www.workbuddy.cn"), {"Authorization": "Bearer " + token}, local=False
        )
        status = self.call("GET", "/openapi/v2/localassistant")
        return {"connected": True, "transport": self.kind, "online": status.get("online", False)}

    def call(self, method, path, body=None):
        if not self.http:
            self.connect()
        data = self.http.request(method, path, body)
        if isinstance(data, dict) and data.get("code", 0) != 0:
            raise BridgeError(
                f'WorkBuddy API returned code {data.get("code")}; verify OAuth scope and desktop assistant status.'
            )
        return data.get("data", data)

    def sessions(self, limit=30):
        return [{"id": self.session_id, **self.connect()}]

    def models(self):
        return [{"note": "Model follows the WorkBuddy desktop assistant configuration."}]

    def create(self, workspace, title="Agent Link", model=None):
        raise BridgeError(
            "WorkBuddy local-assistant API exposes one existing assistant, not arbitrary project sessions. Use session_id=local-assistant; set its workspace in WorkBuddy."
        )

    def send(self, session_id, prompt):
        if session_id != self.session_id:
            raise BridgeError("WorkBuddy session_id must be local-assistant.")
        if not prompt.strip():
            raise BridgeError("prompt cannot be empty.")
        status = self.connect()
        if not status.get("online"):
            raise BridgeError("WorkBuddy desktop local assistant is offline.")
        data = self.call("POST", "/openapi/v2/localassistant/message", {"content": prompt, "msg_type": "text"})
        self.last_message_id = data.get("message_id")
        return {"submitted": True, "session_id": session_id, **data}

    def read(self, session_id, limit=20):
        if session_id != self.session_id:
            raise BridgeError("WorkBuddy session_id must be local-assistant.")
        query = (
            "?message_id=" + quote(self.last_message_id, safe="")
            if self.last_message_id
            else f"?limit={min(max(1, limit), 100)}&offset=0"
        )
        data = self.call("GET", "/openapi/v2/localassistant/message" + query)
        return {
            "session_id": session_id,
            "messages": data.get("messages", []),
            "note": "Messages received are not a completion guarantee; the API does not expose a final task state.",
        }

    def wait(self, session_id, seconds=30):
        if not self.last_message_id:
            raise BridgeError("Send a message first; waiting requires the submitted message cursor.")
        deadline = time.monotonic() + min(max(1, seconds), 45)
        while True:
            data = self.read(session_id)
            if any(m.get("role") == "assistant" for m in data["messages"]):
                return {"status": "response_received", **data}
            if time.monotonic() >= deadline:
                return {"timed_out": True, **data}
            time.sleep(1)

    def close(self):
        pass
