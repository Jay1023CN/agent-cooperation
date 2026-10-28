"""Desktop/ACP agent bridge added by the PAL Agent Link fork."""

import asyncio
import atexit
import json
from pathlib import Path
from typing import Any, Literal, Optional

from mcp.types import TextContent
from pydantic import BaseModel, ConfigDict, Field

from agent_link.acp import ACP
from agent_link.common import BridgeError, load_config
from agent_link.handoff_store import HandoffStore
from agent_link.server import Agents
from tools.shared.base_tool import BaseTool


class AgentLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "status",
        "sessions",
        "models",
        "create",
        "send",
        "read",
        "wait",
        "permissions",
        "permission_reply",
        "handoff_list",
        "handoff_claim",
        "handoff_status",
        "handoff_submit",
    ]
    agent: Optional[Literal["antigravity", "opencode", "qoder", "qodercn", "workbuddy"]] = None
    workspace: Optional[str] = None
    title: str = "PAL Agent Link"
    agent_model: Optional[str] = Field(default=None, description="Agent model ID; not a PAL API-provider model.")
    session_id: Optional[str] = None
    prompt: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)
    seconds: int = Field(default=30, ge=1, le=45)
    permission_id: Optional[str] = None
    option_id: Optional[str] = None
    handoff_db: Optional[str] = Field(
        default=None, description="Existing absolute local handoff SQLite path, shared with the host."
    )
    lineage: Optional[str] = None
    handle: Optional[str] = None
    summary: Optional[str] = None
    evidence: Optional[list[str]] = None


class AgentLinkTool(BaseTool):
    def __init__(self):
        self.agents = Agents(load_config())
        atexit.register(self.agents.close)
        super().__init__()

    def get_name(self) -> str:
        return "agentlink"

    def get_description(self) -> str:
        return (
            "Dispatch tasks to Antigravity desktop sessions, OpenCode desktop services, Qoder/Qoder CN ACP agents, "
            "and the WorkBuddy local assistant. Use action=status to discover connections, create/send to submit, "
            "and read/wait for actual results. Qoder ACP does not control an existing IDE chat. "
            "WorkBuddy needs official OAuth. Submitting may edit workspace files; never automatically retry "
            "an uncertain submission. Use permission_reply only for user-authorized actions."
            " For this existing desktop conversation, handoff_list/claim/status/submit exchange local documents "
            "with a host using handoff_db; they never start a model session or wake an idle conversation."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return AgentLinkRequest.model_json_schema()

    def get_annotations(self) -> dict[str, Any]:
        return {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}

    def requires_model(self) -> bool:
        return False

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return AgentLinkRequest

    async def prepare_prompt(self, request) -> str:
        return ""

    @staticmethod
    def _required(value, name):
        if not value:
            raise BridgeError(f"{name} is required for this action.")
        return value

    def _dispatch(self, request):
        if request.action.startswith("handoff_"):
            return self._handoff(request)
        if request.action == "status":
            return self.agents.status()
        adapter = self.agents.get(self._required(request.agent, "agent"))
        if request.action == "sessions":
            return {"sessions": adapter.sessions(request.limit)}
        if request.action == "models":
            return {"models": adapter.models()}
        if request.action == "create":
            return adapter.create(self._required(request.workspace, "workspace"), request.title, request.agent_model)
        if request.action in ("permissions", "permission_reply"):
            if not isinstance(adapter, ACP):
                raise BridgeError("Permission tools currently support Qoder ACP only.")
            if request.action == "permission_reply":
                return adapter.reply(self._required(request.permission_id, "permission_id"), request.option_id)
            return {"permissions": adapter.permissions(self._required(request.session_id, "session_id"))}
        session_id = self._required(request.session_id, "session_id")
        if request.action == "send":
            return adapter.send(session_id, self._required(request.prompt, "prompt"))
        if request.action == "read":
            return adapter.read(session_id, request.limit)
        return adapter.wait(session_id, request.seconds)

    def _handoff(self, request):
        path = Path(self._required(request.handoff_db, "handoff_db")).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise BridgeError("handoff_db must name an existing absolute local SQLite file shared with the host.")
        # Reuse the existing handoff protocol and leases; PAL adds no scheduler.
        store = HandoffStore(path)
        if request.action == "handoff_list":
            return {"tasks": store.pending()}
        lineage = self._required(request.lineage, "lineage")
        if request.action == "handoff_claim":
            return store.claim(lineage)
        handle = self._required(request.handle, "handle")
        if request.action == "handoff_status":
            return {"state": store.desktop_state(lineage, handle)}
        summary = self._required(request.summary, "summary")
        evidence = request.evidence
        if not summary.strip() or not evidence or not all(item.strip() for item in evidence):
            raise BridgeError("A summary and at least one nonempty evidence item are required.")
        store.submit(lineage, handle, {"summary": summary, "evidence": evidence})
        return {"state": "submitted"}

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        request = AgentLinkRequest.model_validate(arguments)
        result = await asyncio.to_thread(self._dispatch, request)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
