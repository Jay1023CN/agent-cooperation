"""Diagnostics must inspect prerequisites without executing model tasks or exposing credentials."""

import json
from collections import namedtuple
from unittest.mock import Mock

import httpx
import pytest

from arena import doctor
from arena.worker import WorkerError


@pytest.fixture
def diagnostic_environment(tmp_path, monkeypatch):
    config = {
        "server": "http://127.0.0.1:8765",
        "workspace_root": str(tmp_path / "workspaces"),
        "storage_limit_bytes": 5_000_000_000,
        "agents": [{"key": "lead", "name": "Leader", "provider": "codex", "roles": ["leader"]}],
    }
    path = tmp_path / "worker.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(doctor.importlib.metadata, "version", lambda _: "fixture-version")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/fixture/" + name)
    monkeypatch.setattr(
        doctor.shutil, "disk_usage", lambda _: namedtuple("Disk", "total used free")(50_000_000_000, 0, 50_000_000_000)
    )
    monkeypatch.setattr(doctor.os, "access", lambda *_: True)
    monkeypatch.setattr(
        "httpx.get", lambda *_args, **_kwargs: httpx.Response(200, json={"status": "ok", "name": "PAL Arena"})
    )
    return path, config


def check(report, name):
    return next(item for item in report["checks"] if item["name"] == name)


def test_doctor_never_launches_a_cli_or_sends_a_model_prompt(diagnostic_environment, monkeypatch):
    path, _ = diagnostic_environment
    forbidden = Mock(side_effect=AssertionError("diagnostics must not execute tasks"))
    monkeypatch.setattr("arena.worker.run_process", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("agent_link.acp.ACP.send", forbidden)
    monkeypatch.setattr("agent_link.antigravity.Antigravity.send", forbidden)
    monkeypatch.setattr("agent_link.opencode.OpenCode.send", forbidden)
    monkeypatch.setattr("agent_link.workbuddy.WorkBuddy.send", forbidden)
    report = doctor.diagnose(path)
    assert report["agents"][0]["dispatchable"] is True
    assert report["agents"][0]["account_readiness"] == "unknown"
    forbidden.assert_not_called()
    assert "No task or model prompt was sent." in report["limits"]


def test_unknown_subscription_is_not_reported_fully_ready(diagnostic_environment):
    path, _ = diagnostic_environment
    report = doctor.diagnose(path)
    assert report["agents"][0]["account_readiness"] == "unknown"
    assert report["status"] != "ok"
    assert any(item["state"] in ("unknown", "warning") for item in report["checks"])


@pytest.mark.parametrize("content", ["{credential: SECRET_VALUE", '{"agents":"SECRET_VALUE"}', "[]"])
def test_invalid_configuration_does_not_leak_contents_or_exception_values(tmp_path, content):
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="utf-8")
    report = doctor.diagnose(path, probe_transport=False)
    assert check(report, "worker_config")["state"] == "error"
    assert "SECRET_VALUE" not in doctor.render(report)
    assert "SECRET_VALUE" not in doctor.render(report, json_output=True)


def test_invalid_configuration_exception_details_are_suppressed(diagnostic_environment, monkeypatch):
    path, _ = diagnostic_environment
    monkeypatch.setattr("arena.worker.load_config", Mock(side_effect=WorkerError("server rejected bearer TOP_SECRET")))
    report = doctor.diagnose(path)
    assert check(report, "worker_config")["state"] == "error"
    assert "TOP_SECRET" not in json.dumps(report)


def test_read_only_transport_diagnostic_sanitizes_adapter_errors(diagnostic_environment, monkeypatch):
    path, config = diagnostic_environment
    config["agents"][0].update(provider="antigravity", model="example-model", adapter={"token": "ADAPTER_SECRET"})
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(
        "arena.worker.antigravity_services", lambda: [{"url": "https://localhost:1234", "token": "DISCOVERY_SECRET"}]
    )

    class Adapter:
        def connect(self):
            raise WorkerError("upstream credentials: ADAPTER_SECRET DISCOVERY_SECRET")

        def close(self):
            pass

    monkeypatch.setattr("arena.worker.Agents.get", lambda *_: Adapter())
    report = doctor.diagnose(path)
    output = doctor.render(report, json_output=True)
    assert report["agents"][0]["dispatchable"] is False
    assert "ADAPTER_SECRET" not in output
    assert "DISCOVERY_SECRET" not in output
    assert "adapter" not in report["agents"][0]


def test_read_only_transport_diagnostic_keeps_actionable_error_without_credentials(diagnostic_environment, monkeypatch):
    path, config = diagnostic_environment
    config["agents"][0].update(provider="antigravity", model="example-model", adapter={"token": "LOCAL_SECRET"})
    path.write_text(json.dumps(config), encoding="utf-8")

    class Adapter:
        def connect(self):
            raise WorkerError("connection refused; token=LOCAL_SECRET")

        def close(self):
            pass

    monkeypatch.setattr(
        "arena.worker.antigravity_services", lambda: [{"url": "https://localhost:1234", "token": "LOCAL_SECRET"}]
    )
    monkeypatch.setattr("arena.worker.Agents.get", lambda *_: Adapter())
    report = doctor.diagnose(path)
    reason = report["agents"][0]["reason"]
    assert "connection refused" in reason
    assert "LOCAL_SECRET" not in reason


@pytest.mark.parametrize(
    "body,status",
    [
        ({"status": "ok", "name": "Unrelated local service"}, 200),
        ({"status": "ok"}, 200),
        ({"status": "down", "name": "PAL Arena"}, 200),
        ({"status": "ok", "name": "PAL Arena"}, 503),
        (["SECRET_RESPONSE"], 200),
    ],
)
def test_health_mismatch_is_a_warning_not_readiness_or_exception(diagnostic_environment, monkeypatch, body, status):
    path, _ = diagnostic_environment
    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: httpx.Response(status, json=body))
    report = doctor.diagnose(path)
    assert check(report, "server")["state"] == "warning"
    assert "SECRET_RESPONSE" not in doctor.render(report)


def test_skipped_transport_checks_do_not_issue_network_requests(diagnostic_environment, monkeypatch):
    path, _ = diagnostic_environment
    network = Mock(side_effect=AssertionError("network disabled"))
    monkeypatch.setattr("httpx.get", network)
    report = doctor.diagnose(path, probe_transport=False)
    assert check(report, "server")["state"] == "unknown"
    network.assert_not_called()
