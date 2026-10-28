"""Verify the PAL tool contract without launching real agents."""

import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agent_link.common import BridgeError
from tools.agentlink import AgentLinkRequest, AgentLinkTool


def test_agentlink_schema_and_annotations():
    tool = AgentLinkTool()
    assert tool.requires_model() is False
    assert tool.get_name() == "agentlink"
    assert tool.get_annotations()["idempotentHint"] is False
    assert "agent_model" in tool.get_input_schema()["properties"]
    with pytest.raises(ValidationError):
        AgentLinkRequest(action="send", unknown="no")


@pytest.mark.asyncio
async def test_agentlink_dispatches_without_provider_model():
    tool = AgentLinkTool()
    adapter = Mock()
    adapter.send.return_value = {"submitted": True, "session_id": "session-1"}
    tool.agents.get = Mock(return_value=adapter)
    result = await tool.execute({"action": "send", "agent": "qoder", "session_id": "session-1", "prompt": "hello"})
    assert json.loads(result[0].text)["submitted"] is True
    adapter.send.assert_called_once_with("session-1", "hello")


@pytest.mark.asyncio
async def test_agentlink_validates_before_mutation():
    tool = AgentLinkTool()
    adapter = Mock()
    tool.agents.get = Mock(return_value=adapter)
    with pytest.raises(BridgeError, match="session_id"):
        await tool.execute({"action": "send", "agent": "qoder", "prompt": "hello"})
    adapter.send.assert_not_called()


@pytest.mark.asyncio
async def test_agentlink_uses_agent_model_field():
    tool = AgentLinkTool()
    adapter = Mock()
    adapter.create.return_value = {"id": "session-1"}
    tool.agents.get = Mock(return_value=adapter)
    await tool.execute({"action": "create", "agent": "antigravity", "workspace": "/scratch", "agent_model": "model-1"})
    adapter.create.assert_called_once_with("/scratch", "PAL Agent Link", "model-1")
