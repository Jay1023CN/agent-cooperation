from __future__ import annotations

import time
from urllib.parse import quote

from .common import BridgeError, JsonHTTP, basic_auth, unwrap, workspace_path
from .discovery import opencode_service


class OpenCode:
    kind = "desktop-http"

    def __init__(self, config=None):
        self.config = config or {}
        self.http = None
        self.version = None

    def connect(self):
        service = opencode_service(self.config)
        headers = {"Authorization": basic_auth(service["password"])} if service.get("password") else {}
        self.http = JsonHTTP(service["url"], headers)
        # v2 and v1 use different contracts; never infer success from a SPA HTML response.
        try:
            info = self.http.request("GET", "/api/info")
            self.version = 2
        except BridgeError:
            info = self.http.request("GET", "/global/health")
            if not info.get("healthy"):
                raise BridgeError("OpenCode health check failed.")
            self.version = 1
        return {
            "connected": True,
            "transport": self.kind,
            "api_version": self.version,
            "server_version": info.get("version"),
            "url": self.http.url,
        }

    def request(self, method, path, body=None, timeout=30):
        if not self.http:
            self.connect()
        return self.http.request(method, path, body, timeout)

    def sessions(self, limit=30):
        if not self.http:
            self.connect()
        path = "/api/session" if self.version == 2 else "/session"
        values = unwrap(self.request("GET", path + f"?limit={max(1, min(limit, 100))}"))
        return [
            {
                "id": x["id"],
                "title": x.get("title"),
                "outcome": x.get("outcome"),
                "directory": x.get("location", {}).get("directory") or x.get("directory"),
            }
            for x in values[:limit]
        ]

    def models(self):
        if not self.http:
            self.connect()
        if self.version == 1:
            data = self.request("GET", "/config/providers")
            return [{"provider": p.get("id"), "models": list(p.get("models", {}))} for p in data.get("providers", [])]
        return [
            {
                "id": m.get("id"),
                "provider": m.get("providerID"),
                "name": m.get("name"),
                "enabled": m.get("enabled"),
                "variants": [v["id"] for v in m.get("variants", [])],
            }
            for m in unwrap(self.request("GET", "/api/model"))
        ]

    def create(self, workspace, title="Agent Link", model=None):
        path = str(workspace_path(workspace))
        if not self.http:
            self.connect()
        if self.version == 2:
            body = {"title": title, "location": {"directory": path}}
            if model:
                provider, identifier = model.split("/", 1)
                body["model"] = {"providerID": provider, "id": identifier}
            return unwrap(self.request("POST", "/api/session", body))
        return self.request("POST", "/session?directory=" + quote(path, safe=""), {"title": title})

    def send(self, session_id, prompt):
        if not prompt.strip():
            raise BridgeError("prompt cannot be empty.")
        if not self.http:
            self.connect()
        sid = quote(session_id, safe="")
        if self.version == 2:
            result = unwrap(self.request("POST", f"/api/session/{sid}/prompt", {"text": prompt}))
        else:
            result = self.request("POST", f"/session/{sid}/prompt_async", {"parts": [{"type": "text", "text": prompt}]})
        return {"submitted": True, "session_id": session_id, "result": result}

    def read(self, session_id, limit=20):
        if not self.http:
            self.connect()
        sid = quote(session_id, safe="")
        if self.version == 1:
            info = self.request("GET", f"/session/{sid}")
            messages = self.request("GET", f"/session/{sid}/message?limit={max(1, min(limit, 100))}")
            return {"session_id": session_id, "info": info, "messages": messages}
        info = unwrap(self.request("GET", f"/api/session/{sid}"))
        exported = unwrap(self.request("GET", f"/api/experimental/session/{sid}/export?sanitize=false"))
        messages = []
        for message in exported.get("messages", [])[-max(1, min(limit, 100)) :]:
            entry = {"id": message.get("id"), "type": message.get("type"), "finish": message.get("finish")}
            if message.get("type") == "user":
                entry["text"] = message.get("text", "")
            elif message.get("type") == "assistant":
                entry["text"] = "".join(
                    p.get("text", "") for p in message.get("content", []) if p.get("type") == "text"
                )
            if message.get("error"):
                error = message["error"]
                entry["error"] = {"type": error.get("type"), "status": error.get("status")}
            messages.append(entry)
        return {
            "session_id": session_id,
            "outcome": info.get("outcome"),
            "time": info.get("time"),
            "messages": messages,
        }

    def wait(self, session_id, seconds=30):
        deadline = time.monotonic() + max(1, min(seconds, 45))
        while True:
            data = self.read(session_id)
            if data.get("outcome") in ("succeeded", "failed", "cancelled"):
                return data
            if time.monotonic() >= deadline:
                return {"timed_out": True, **data}
            time.sleep(1)

    def close(self):
        pass
