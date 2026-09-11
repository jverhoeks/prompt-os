from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.tools.mcp import MCPAgentTool, MCPClient

from .model_client import LiteLLMConfig, create_model


GENERIC_RUNTIME_POLICY = """Operate the service described below.
Only perform services described by the business description; politely decline unrelated requests.
Use the supplied fundamental services for facts, time, calculations and persistence.
Do not invent stored facts or calculated values.
When no data contract is promoted, new records belong in the inbox as flexible JSON.
When structured presentation materially improves a result, call view.present with the smallest useful generic view and also provide a concise textual answer.
The business description is authoritative for product behaviour.
"""


class StrandsSession:
    def __init__(
        self,
        config: LiteLLMConfig,
        *,
        app_id: str,
        functionality: str,
        database: Path,
        contract_root: Path,
        contract_schema: Path,
        tool_catalog: Path,
        capabilities: tuple[str, ...],
        timezone: str = "UTC",
        debug: bool = False,
    ) -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=_mcp_server_arguments(
                app_id=app_id,
                database=database,
                contract_root=contract_root,
                contract_schema=contract_schema,
                timezone=timezone,
                debug=debug,
            ),
        )
        self._client = MCPClient(lambda: stdio_client(parameters))
        self._model = create_model(config)
        self._system_prompt = f"{GENERIC_RUNTIME_POLICY}\n\n{functionality}"
        self._allowed_tools = _allowed_tool_names(tool_catalog, capabilities)
        self._tool_names: dict[str, str] = {}
        self.agent: Agent | None = None

    def __enter__(self) -> "StrandsSession":
        self._client.start()
        tools = _model_tools(
            self._client.list_tools_sync(), self._client, allowed_names=self._allowed_tools
        )
        self._tool_names = {tool.tool_name: tool.mcp_tool.name for tool in tools}
        self.agent = Agent(
            model=self._model,
            system_prompt=self._system_prompt,
            tools=tools,
            callback_handler=None,
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._client.stop(exc_type, exc, traceback)

    def send(self, user_message: str) -> dict[str, Any]:
        if self.agent is None:
            raise RuntimeError("session is not open")
        message_count = len(self.agent.messages)
        result = self.agent(user_message)
        new_messages = self.agent.messages[message_count:]
        return {
            "reply": str(result),
            "tool_calls": _tool_calls(new_messages, tool_names=self._tool_names),
        }


def run_turn(
    config: LiteLLMConfig,
    *,
    app_id: str,
    functionality: str,
    user_message: str,
    database: Path,
    contract_root: Path,
    contract_schema: Path,
    tool_catalog: Path,
    capabilities: tuple[str, ...],
    timezone: str = "UTC",
) -> dict[str, Any]:
    with StrandsSession(
        config,
        app_id=app_id,
        functionality=functionality,
        database=database,
        contract_root=contract_root,
        contract_schema=contract_schema,
        tool_catalog=tool_catalog,
        capabilities=capabilities,
        timezone=timezone,
    ) as session:
        return session.send(user_message)


def _tool_calls(
    messages: list[dict[str, Any]], *, tool_names: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    tool_names = tool_names or {}
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for message in messages:
        for block in message.get("content", []):
            use = block.get("toolUse") if isinstance(block, dict) else None
            if use:
                item = {
                    "id": use.get("toolUseId"),
                    "name": tool_names.get(use.get("name"), use.get("name")),
                    "arguments": use.get("input", {}),
                }
                calls.append(item)
                if item["id"]:
                    by_id[item["id"]] = item
            result = block.get("toolResult") if isinstance(block, dict) else None
            if result:
                target = by_id.get(result.get("toolUseId"))
                if target is not None:
                    target["status"] = result.get("status")
                    target["result"] = result.get("content")
    return calls


def _model_tools(
    tools: list[MCPAgentTool],
    client: MCPClient,
    *,
    allowed_names: set[str],
) -> list[MCPAgentTool]:
    """Expose protocol-safe aliases while retaining each MCP tool's wire name."""
    aliases: set[str] = set()
    adapted: list[MCPAgentTool] = []
    for tool in tools:
        if tool.mcp_tool.name not in allowed_names:
            continue
        alias = _model_tool_name(tool.tool_name)
        if alias in aliases:
            raise RuntimeError(f"MCP tool names collide after model-safe normalization: {alias!r}")
        aliases.add(alias)
        adapted.append(MCPAgentTool(tool.mcp_tool, client, name_override=alias))
    return adapted


def _allowed_tool_names(tool_catalog: Path, capabilities: tuple[str, ...]) -> set[str]:
    payload = json.loads(tool_catalog.read_text(encoding="utf-8"))
    records = payload.get("tools") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"{tool_catalog}: tools must be a list")
    allowed_blocks = set(capabilities) | {"data-contract", "presentation"}
    return {
        record["name"]
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("name"), str)
        and record.get("block") in allowed_blocks
    }


def _model_tool_name(name: str) -> str:
    alias = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    if len(alias) <= 64:
        return alias
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return f"{alias[:55]}_{digest}"


def _mcp_server_arguments(
    *,
    app_id: str,
    database: Path,
    contract_root: Path,
    contract_schema: Path,
    timezone: str,
    debug: bool,
) -> list[str]:
    return [
        "-m",
        "prompt_os.mcp_server",
        "--app",
        app_id,
        "--database",
        str(database),
        "--contracts",
        str(contract_root),
        "--contract-schema",
        str(contract_schema),
        "--timezone",
        timezone,
        "--log-level",
        "DEBUG" if debug else "ERROR",
    ]
