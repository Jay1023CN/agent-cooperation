from __future__ import annotations

import os
from urllib.parse import quote

from .common import BridgeError, JsonHTTP, workspace_path
from .discovery import antigravity_services


class Antigravity:
    kind = "desktop-local-connect-rpc"
    service = "/exa.language_server_pb.LanguageServerService/"

    def __init__(self, config=None):
        self.config = config or {}
        self.http = None
        self.session_models = {}

    def connect(self):
        if self.config.get("url"):
            token = os.getenv(self.config.get("token_env", "ANTIGRAVITY_CSRF_TOKEN"))
            if not token:
                raise BridgeError("Configured Antigravity URL needs a token_env value.")
            candidates = [{"url": self.config["url"], "token": token}]
        else:
            candidates = antigravity_services()
        for candidate in candidates:
            http = JsonHTTP(
                candidate["url"],
                {"x-codeium-csrf-token": candidate["token"], "Connect-Protocol-Version": "1"},
                self_signed=True,
            )
            try:
                http.request("POST", self.service + "ListProjects", {}, timeout=3)
            except BridgeError:
                continue
            self.http = http
            return {
                "connected": True,
                "transport": self.kind,
                "url": http.url,
                "note": "Internal desktop RPC; validate after application updates.",
            }
        raise BridgeError("Antigravity desktop service not found. Open the desktop app; no CLI fallback is used.")

    def rpc(self, method, payload=None, timeout=30):
        if not self.http:
            self.connect()
        return self.http.request("POST", self.service + method, payload or {}, timeout)

    def models(self):
        configs = self.rpc("GetCascadeModelConfigData").get("clientModelConfigs", [])
        return [
            {"id": c.get("modelId"), "name": c.get("label"), "model": c.get("modelOrAlias", {}).get("model")}
            for c in configs
        ]

    def create(self, workspace, title="Agent Link", model=None):
        if not model:
            raise BridgeError("Choose a model from agent_models and pass it to agent_create.")
        selected = next((m for m in self.models() if model in (m["id"], m["name"], m["model"])), None)
        if not selected:
            raise BridgeError("Unknown Antigravity model; query agent_models again.")
        result = self.rpc(
            "StartCascade", {"source": 18, "trajectoryType": 17, "workspaceUris": [workspace_path(workspace).as_uri()]}
        )
        sid = result["cascadeId"]
        self.session_models[sid] = selected["model"]
        return {"id": sid}

    def sessions(self, limit=30):
        values = self.rpc("GetAllCascadeTrajectories", {"excludeSubtrajectories": True}).get("trajectorySummaries", {})
        rows = [
            {
                "id": key,
                "title": value.get("summary"),
                "status": value.get("status"),
                "modified": value.get("lastModifiedTime"),
            }
            for key, value in values.items()
        ]
        return sorted(rows, key=lambda x: x.get("modified") or "", reverse=True)[:limit]

    def send(self, session_id, prompt):
        if not prompt.strip():
            raise BridgeError("prompt cannot be empty.")
        model = self.session_models.get(session_id) or self.config.get("model")
        if not model:
            raise BridgeError(
                "Create a session with a model first, or configure antigravity.model using the model enum from agent_models."
            )
        current = self.read(session_id)
        if current["status"] not in ("CASCADE_RUN_STATUS_IDLE", "CASCADE_RUN_STATUS_UNSPECIFIED"):
            raise BridgeError("Antigravity session is busy.")
        self.rpc(
            "SendUserCascadeMessage",
            {
                "cascadeId": session_id,
                "items": [{"text": prompt}],
                "cascadeConfig": {"plannerConfig": {"planModel": model, "requestedModel": {"model": model}}},
            },
        )
        return {"submitted": True, "session_id": session_id}

    def read(self, session_id, limit=20):
        data = self.rpc("GetCascadeTrajectory", {"cascadeId": session_id, "verbosity": 3})
        messages = []
        for step in data.get("trajectory", {}).get("steps", [])[-limit:]:
            entry = {"type": step.get("type"), "status": step.get("status")}
            if "plannerResponse" in step:
                entry["text"] = step["plannerResponse"].get("response", "")
            elif "userInput" in step:
                entry["text"] = step["userInput"].get("userResponse", "")
            elif "errorMessage" in step:
                entry["error"] = step["errorMessage"].get("error", {}).get("shortError")
            messages.append(entry)
        return {
            "session_id": session_id,
            "status": data.get("status", "CASCADE_RUN_STATUS_UNSPECIFIED"),
            "messages": messages,
            "local_url": self.http.url + "/c/" + quote(session_id, safe=""),
        }

    def wait(self, session_id, seconds=30):
        seconds = min(max(seconds, 1), 45)
        data = self.rpc(
            "WaitForConversationFullyIdle",
            {
                "conversationId": session_id,
                "inactivityTimeoutSeconds": seconds,
                "stabilizationDurationSeconds": 1,
                "returnOnExecutorError": True,
            },
            timeout=seconds + 5,
        )
        return {"timed_out": data.get("timedOut", False), **self.read(session_id)}

    def close(self):
        pass
