from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from agent_link.acp import ACP
from agent_link.common import BridgeError, JsonHTTP, local_url, workspace_path
from agent_link.discovery import opencode_service, qoder_command
from agent_link.opencode import OpenCode
from agent_link.workbuddy import WorkBuddy


class Handler(BaseHTTPRequestHandler):
    mutations = 0

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/steal")
            self.end_headers()
            return
        if self.path == "/api/info":
            value = {"version": "2.fixture"}
        elif self.path.startswith(("/api/session/", "/api/experimental/session/")):
            if "/export" in self.path:
                value = {
                    "data": {
                        "messages": [
                            {"id": "u1", "type": "user", "text": "hello"},
                            {
                                "id": "a1",
                                "type": "assistant",
                                "finish": "stop",
                                "content": [
                                    {"type": "reasoning", "text": "PRIVATE_THOUGHT"},
                                    {"type": "text", "text": "PUBLIC_REPLY"},
                                ],
                            },
                            {"id": "i1", "type": "idle", "outcome": "succeeded"},
                        ]
                    }
                }
            else:
                value = {"data": {"id": "s1", "outcome": "succeeded"}}
        else:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"message":"secret-token-never-return"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())

    def do_POST(self):
        type(self).mutations += 1
        size = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(size)
        if self.path == "/slow":
            time.sleep(0.2)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        except OSError:
            pass


class HTTPContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_loopback_only(self):
        for url in ("https://example.com", "http://127.0.0.1.evil.example", "http://user:password@localhost"):
            with self.assertRaises(BridgeError):
                local_url(url)
        self.assertEqual(local_url("http://[::1]:123/"), "http://[::1]:123")

    def test_no_credentials_forwarded_on_redirect(self):
        with self.assertRaises(BridgeError):
            JsonHTTP(self.url, {"Authorization": "secret"}).request("GET", "/redirect")

    def test_upstream_secret_not_returned(self):
        with self.assertRaises(BridgeError) as context:
            JsonHTTP(self.url).request("GET", "/forbidden")
        self.assertIn("401", str(context.exception))
        self.assertNotIn("secret-token", str(context.exception))

    def test_uncertain_mutation_not_retried(self):
        Handler.mutations = 0
        with self.assertRaises(BridgeError):
            JsonHTTP(self.url).request("POST", "/slow", {"edit": True}, timeout=0.03)
        self.assertEqual(Handler.mutations, 1)

    def test_opencode_v2_public_response(self):
        agent = OpenCode({"url": self.url})
        self.assertEqual(agent.connect()["api_version"], 2)
        result = agent.read("s1")
        self.assertEqual(result["messages"][1]["text"], "PUBLIC_REPLY")
        self.assertNotIn("PRIVATE_THOUGHT", json.dumps(result))


class DiscoveryContracts(unittest.TestCase):
    def test_xdg_state_service(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "opencode/service.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"url": "http://127.0.0.1:123", "password": "local-secret"}))
            with patch.dict(os.environ, {"XDG_STATE_HOME": root}):
                self.assertEqual(opencode_service({})["url"], "http://127.0.0.1:123")

    def test_command_requires_argv_array(self):
        with self.assertRaises(BridgeError):
            qoder_command("qoder", {"command": "qoder; unsafe-shell"})
        self.assertEqual(qoder_command("qoder", {"command": ["custom-agent", "--flag"]}), ["custom-agent", "--flag"])

    def test_workspace_must_exist_and_be_absolute(self):
        with self.assertRaises(BridgeError):
            workspace_path("relative-folder")


class ACPContracts(unittest.TestCase):
    def test_permission_roundtrip_and_streamed_reply(self):
        fixture = Path(__file__).with_name("fake_agentlink_acp.py")
        agent = ACP("qoder", {"command": [sys.executable, str(fixture)]})
        with tempfile.TemporaryDirectory() as workspace:
            try:
                agent.connect()
                sid = agent.create(workspace)["id"]
                agent.send(sid, "edit fixture")
                state = agent.wait(sid, 3)
                self.assertEqual(state["status"], "waiting_permission")
                permission = state["permissions"][0]
                self.assertEqual(permission["options"][0]["optionId"], "once")
                agent.reply(permission["id"], "once")
                result = agent.wait(sid, 3)
                self.assertEqual(result["text"], "ALLOWED")
                self.assertEqual(result["status"], "completed")
                with self.assertRaises(BridgeError):
                    agent.reply(permission["id"], "once")
            finally:
                agent.close()


class WorkBuddyContracts(unittest.TestCase):
    def test_missing_oauth_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(BridgeError, "OAuth access token missing"):
                WorkBuddy().connect()

    def test_api_contract_send_and_cursor(self):
        class FakeHTTP:
            def request(self, method, path, body=None):
                if method == "POST":
                    self.assert_body = body
                    return {"code": 0, "data": {"message_id": "m1"}}
                return {"code": 0, "data": {"messages": [{"role": "assistant", "content": ["hello"]}]}}

        agent = WorkBuddy()
        agent.http = FakeHTTP()
        with patch.object(agent, "connect", return_value={"online": True}):
            self.assertEqual(agent.send("local-assistant", "hello")["message_id"], "m1")
        self.assertEqual(agent.http.assert_body, {"content": "hello", "msg_type": "text"})
        self.assertEqual(agent.wait("local-assistant", 1)["status"], "response_received")


if __name__ == "__main__":
    unittest.main()
