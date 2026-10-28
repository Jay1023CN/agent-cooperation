import json
import sys
from unittest.mock import patch

from agent_link.preflight import preflight
from agent_link.server import main


def test_preflight_does_not_disclose_config_or_start_agents(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"workbuddy": {"token": "private-value"}}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["pal-agent-link", "--preflight", "--config", str(config)])
    with patch("agent_link.server.Agents", side_effect=AssertionError("must not connect")):
        main()
    output = capsys.readouterr().out
    assert "private-value" not in output
    assert json.loads(output)["checks"]["configuration"]["ok"] is True


def test_preflight_reports_unreadable_config_without_exposing_content(tmp_path):
    config = tmp_path / "bad.json"
    config.write_text("not json secret", encoding="utf-8")
    result = preflight(str(config))
    assert result["checks"]["configuration"]["ok"] is False
    assert "secret" not in str(result)
