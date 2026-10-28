"""Verify the PAL tool contract without launching real agents."""

import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agent_link.common import BridgeError
from agent_link.handoff_store import HandoffError, HandoffStore
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


@pytest.mark.asyncio
async def test_existing_pal_conversation_exchanges_document_without_launching_agent(tmp_path):
    store = HandoffStore(tmp_path / "handoff.db")
    store.create("design", "design-1", {"workspace": str(tmp_path / "review"), "prompt": "Review the UI plan"})
    tool = AgentLinkTool()
    tool.agents.get = Mock(side_effect=AssertionError("No other model session should be used"))
    common = {"handoff_db": str(tmp_path / "handoff.db")}
    listed = json.loads((await tool.execute({"action": "handoff_list", **common}))[0].text)
    assert listed["tasks"][0]["lineage"] == "design"
    claimed = json.loads((await tool.execute({"action": "handoff_claim", "lineage": "design", **common}))[0].text)
    owned = {**common, "lineage": "design", "handle": claimed["handle"]}
    assert claimed["payload"]["prompt"] == "Review the UI plan"
    with pytest.raises(BridgeError, match="evidence"):
        await tool.execute({"action": "handoff_submit", "summary": "Draft", **owned})
    await tool.execute({"action": "handoff_submit", "summary": "UI plan", "evidence": ["docs/ui.md"], **owned})
    assert store.inspect("design")["result"] == {"summary": "UI plan", "evidence": ["docs/ui.md"]}
    with pytest.raises(HandoffError, match="Invalid"):
        await tool.execute({"action": "handoff_status", **owned, "handle": "other-conversation"})


@pytest.mark.asyncio
async def test_pal_handoff_requires_existing_database_and_respects_expiry(tmp_path):
    tool = AgentLinkTool()
    database = tmp_path / "missing.db"
    with pytest.raises(BridgeError, match="existing absolute"):
        await tool.execute({"action": "handoff_list", "handoff_db": str(database)})
    assert not database.exists()
    store = HandoffStore(database)
    store.create("expired", "task", {})
    store.expire("expired")
    with pytest.raises(HandoffError, match="missing or already claimed"):
        await tool.execute({"action": "handoff_claim", "handoff_db": str(database), "lineage": "expired"})
