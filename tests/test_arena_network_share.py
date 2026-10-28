import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arena import network
from arena.api import create_app


def running(name="mac-mini.tail1234.ts.net."):
    return {"BackendState": "Running", "Self": {"DNSName": name}}


@pytest.fixture
def tailscale(monkeypatch):
    calls = []

    def fake(result):
        monkeypatch.setattr(network, "find_tailscale", lambda: "/usr/bin/tailscale")
        monkeypatch.setattr(network, "_status", lambda cli: result["status"])

        def run(command, **kwargs):
            calls.append(command)
            if result.get("timeout"):
                raise subprocess.TimeoutExpired(command, 25, output=result.get("output", ""), stderr="")
            return SimpleNamespace(returncode=result.get("code", 0), stdout=result.get("output", ""), stderr="")

        monkeypatch.setattr(network.subprocess, "run", run)

    fake.calls = calls
    return fake


def test_missing_tailscale_points_to_download(monkeypatch, tmp_path):
    monkeypatch.setattr(network, "find_tailscale", lambda: None)
    result = network.share(8765, tmp_path)
    assert result["state"] == "not_installed" and result["action_url"] == "https://tailscale.com/download"
    assert network.saved(tmp_path)["state"] == "not_installed"


def test_signed_out_tailscale_asks_to_sign_in(tailscale, tmp_path):
    tailscale({"status": {"BackendState": "NeedsLogin"}})
    assert network.share(8765, tmp_path)["state"] == "login_required"
    assert not [c for c in tailscale.calls if c[0] == "/usr/bin/tailscale"]


def test_share_serves_the_local_port_and_reports_the_https_name(tailscale, tmp_path):
    tailscale({"status": running()})
    result = network.share(8799, tmp_path)
    assert [c for c in tailscale.calls if c[0] == "/usr/bin/tailscale"] == [
        ["/usr/bin/tailscale", "serve", "--bg", "http://127.0.0.1:8799"]
    ]
    assert result["state"] == "shared" and result["url"] == "https://mac-mini.tail1234.ts.net"
    assert network.saved(tmp_path)["url"] == result["url"]


@pytest.mark.parametrize("timeout", [False, True])
def test_first_share_returns_the_enable_https_link(tailscale, tmp_path, timeout):
    link = "https://login.tailscale.com/f/serve?node=abc123"
    tailscale({"status": running(), "code": 1, "timeout": timeout, "output": f"Serve is not enabled.\n{link}\n"})
    result = network.share(8765, tmp_path)
    assert result["state"] == "needs_enable" and result["action_url"] == link


def test_address_endpoint_and_share_is_center_only(tmp_path):
    app = create_app(tmp_path / "arena")
    token = (tmp_path / "arena" / "owner-token.txt").read_text(encoding="utf-8")
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        assert client.get("/api/setup/address").json()["state"] == "not_shared"
        network._save({"state": "shared", "url": "https://mac-mini.tail1234.ts.net"}, tmp_path / "arena")
        assert client.get("/api/setup/address").json()["url"] == "https://mac-mini.tail1234.ts.net"
        # TestClient connects as a non-local client, like a request forwarded from another computer.
        assert client.post("/api/setup/share").status_code == 403
        client.headers.pop("Authorization")
        assert client.get("/api/setup/address").status_code == 401
