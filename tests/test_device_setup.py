import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from agent_link import device_setup, setup_helper


def response(payload, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(payload), "")


@pytest.fixture
def running_status():
    return {
        "BackendState": "Running",
        "Self": {"Online": True, "TailscaleIPs": ["100.64.0.2"]},
        "CurrentTailnet": {"Name": "example.ts.net"},
        "Peer": {"secret-key": {"DNSName": "mini.example.ts.net.", "TailscaleIPs": ["100.64.0.3"]}},
    }


def test_missing_client_offers_download(monkeypatch):
    monkeypatch.setattr(device_setup, "find_tailscale", lambda: None)
    result = device_setup.inspect_device()
    assert result["network"]["state"] == "not_installed"
    assert result["next_action"]["url"] == "https://tailscale.com/download"


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ("NeedsLogin", "login_required"),
        ("NeedsMachineAuth", "approval_required"),
        ("Starting", "starting"),
        ("Stopped", "disconnected"),
        ("unexpected", "unavailable"),
    ],
)
def test_client_states(monkeypatch, backend, expected):
    monkeypatch.setattr(device_setup, "find_tailscale", lambda: "tailscale")
    monkeypatch.setattr(device_setup, "_run", lambda *args: response({"BackendState": backend}))
    assert device_setup.inspect_device()["network"]["state"] == expected


def test_network_and_center_checks(monkeypatch, running_status):
    calls = []
    monkeypatch.setattr(device_setup, "find_tailscale", lambda: "tailscale")

    def run(cli, args):
        calls.append(args)
        return response(running_status) if args[0] == "status" else response({})

    monkeypatch.setattr(device_setup, "_run", run)
    result = device_setup.inspect_device("EXAMPLE.ts.net.", "mini.example.ts.net")
    assert result["network"]["matches_expected"] is True
    assert result["center"]["state"] == "reachable"
    assert result["center"]["service"] == "not_configured"
    assert calls[-1][-1] == "mini.example.ts.net"
    assert "secret-key" not in json.dumps(result)
    wrong = device_setup.inspect_device("other.ts.net", "mini.example.ts.net")
    assert wrong["network"]["state"] == "wrong_network"
    assert wrong["center"]["state"] == "not_checked"
    assert device_setup.inspect_device(center="attacker.example")["center"]["state"] == "not_found"


def test_missing_network_identity_is_not_a_match(monkeypatch, running_status):
    del running_status["CurrentTailnet"]
    monkeypatch.setattr(device_setup, "find_tailscale", lambda: "tailscale")
    monkeypatch.setattr(device_setup, "_run", lambda *args: response(running_status))
    result = device_setup.inspect_device("example.ts.net")
    assert result["network"]["state"] == "network_unverified"
    assert result["network"]["matches_expected"] is None


def test_cli_failure_and_offline_node(monkeypatch, running_status):
    monkeypatch.setattr(device_setup, "find_tailscale", lambda: "tailscale")
    monkeypatch.setattr(device_setup, "_run", lambda *args: response({}, 1))
    assert device_setup.inspect_device()["network"]["state"] == "unavailable"
    running_status["Self"]["Online"] = False
    monkeypatch.setattr(device_setup, "_run", lambda *args: response(running_status))
    assert device_setup.inspect_device()["network"]["state"] == "disconnected"


def test_unreachable_center(monkeypatch, running_status):
    monkeypatch.setattr(device_setup, "find_tailscale", lambda: "tailscale")
    monkeypatch.setattr(
        device_setup, "_run", lambda cli, args: response(running_status) if args[0] == "status" else response({}, 1)
    )
    result = device_setup.inspect_device("example.ts.net", "100.64.0.3")
    assert result["network"]["state"] == "connected"
    assert result["center"]["state"] == "unreachable"


def test_center_service_is_checked_only_for_a_visible_peer(monkeypatch, running_status):
    monkeypatch.setattr(device_setup, "find_tailscale", lambda: "tailscale")
    monkeypatch.setattr(
        device_setup, "_run", lambda cli, args: response(running_status) if args[0] == "status" else response({}, 1)
    )
    seen = []
    monkeypatch.setattr(device_setup, "_service_state", lambda url: seen.append(url) or "ready")
    result = device_setup.inspect_device("example.ts.net", center_url="https://mini.example.ts.net")
    assert result["center"] == {"state": "unreachable", "service": "ready"}
    assert seen == ["https://mini.example.ts.net"]
    result = device_setup.inspect_device(center_url="https://other.example.ts.net")
    assert result["center"] == {"state": "not_found", "service": "not_checked"}
    assert len(seen) == 1
    with pytest.raises(ValueError):
        device_setup.inspect_device(center="mini.example.ts.net", center_url="https://other.example.ts.net")
    with pytest.raises(ValueError):
        device_setup.inspect_device(center_url="http://mini.example.ts.net")


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (200, {"status": "ok", "checks": {"db": "ok", "migrations": "ok"}}, "ready"),
        (503, {"status": "not_ready"}, "not_ready"),
        (200, {"status": "ok"}, "unavailable"),
        (302, {}, "unavailable"),
    ],
)
def test_center_service_readiness(monkeypatch, status, payload, expected):
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            assert url == "https://mini.example.ts.net/readyz"
            return httpx.Response(status, json=payload)

    monkeypatch.setattr(device_setup.httpx, "Client", FakeClient)
    assert device_setup._service_state("https://mini.example.ts.net") == expected


def test_macos_app_runs_as_cli(monkeypatch):
    monkeypatch.setattr(device_setup.platform, "system", lambda: "Darwin")
    captured = {}

    def run(args, **kwargs):
        captured.update(kwargs)
        return response({})

    monkeypatch.setattr(device_setup.subprocess, "run", run)
    device_setup._run("/Applications/Tailscale.app/Contents/MacOS/Tailscale", ["status", "--json"])
    assert captured["env"]["TAILSCALE_BE_CLI"] == "1"


def test_helper_accepts_only_configured_workbench(monkeypatch):
    monkeypatch.setattr(setup_helper, "inspect_device", lambda *args: {"scope": "local_device"})
    client = TestClient(setup_helper.create_app(["https://workbench.example.ts.net"]))
    origin = {"Origin": "https://workbench.example.ts.net", "Host": "127.0.0.1:8792"}
    result = client.get("/v1/status", headers=origin)
    assert result.json() == {"scope": "local_device"}
    assert result.headers["access-control-allow-origin"] == origin["Origin"]
    assert client.get("/v1/status", headers={**origin, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/v1/status", headers={**origin, "Host": "evil.example"}).status_code == 403
    assert client.get("/v1/status", headers={"Host": "localhost:8792"}).status_code == 403
    preflight = client.options(
        "/v1/status",
        headers={**origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Private-Network": "true"},
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-private-network"] == "true"
    assert client.options("/v1/status", headers={**origin, "Access-Control-Request-Method": "POST"}).status_code == 403


def test_center_status_requires_member_identity(tmp_path, monkeypatch):
    from arena.api import create_app

    monkeypatch.setattr(
        device_setup, "inspect_device", lambda: {"scope": "local_device", "network": {"state": "connected"}}
    )
    app = create_app(tmp_path)
    token = (tmp_path / "owner-token.txt").read_text(encoding="utf-8")
    with TestClient(app) as client:
        assert client.get("/api/setup/center").status_code == 401
        response = client.get("/api/setup/center", headers={"Authorization": f"Bearer {token}"})
        assert response.json() == {"scope": "workbench_host", "network": {"state": "connected"}}


@pytest.mark.parametrize(
    "origin", ["*", "null", "http://evil.example", "https://x.example/path", "https://x.example?x=1"]
)
def test_invalid_origins_are_rejected(origin):
    with pytest.raises(ValueError):
        setup_helper.create_app([origin])
