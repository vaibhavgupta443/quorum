"""Tools: what the agent can DO. Everything the agent can call is a `Tool`, wherever it actually runs.

    MCP tools    -> run inside the MCP server process (workspace data: people, tasks, calendar)
    local tools  -> would run inside our app if they needed data the server doesn't have (none are needed for transcripts)

The agent loop doesn't care which is which. That is the whole point of one `Tool` shape.
"""

from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from mcp import Client, StdioServerParameters

from .config import ROOT


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON schema of the arguments
    call: Callable[[dict[str, Any]], Awaitable[ToolResult]]

    def to_api(self) -> dict[str, Any]:
        """What the model sees about a tool: ONLY its name, description and argument schema."""
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


# ------------------------------------------------------------------ MCP client


class MCPToolbox:
    """Starts the MCP server as a subprocess, connects to it, and turns its tools into `Tool`s.

    `server` can be a ready-made MCPServer object (used in tests: runs in-process, no subprocess).
    """

    def __init__(self, server: Any | None = None):
        self._server = server or _server_params()
        self._stack = AsyncExitStack()
        self.client: Client | None = None
        self.tools: dict[str, Tool] = {}

    async def __aenter__(self) -> "MCPToolbox":
        self.client = await self._stack.enter_async_context(Client(self._server))
        # Discovery: ask the server what it offers. We never hard-code the tool list.
        for t in (await self.client.list_tools()).tools:
            self.tools[t.name] = Tool(t.name, t.description or "", t.input_schema, self._caller(t.name))
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.aclose()

    def _caller(self, name: str) -> Callable[[dict[str, Any]], Awaitable[ToolResult]]:
        async def call(args: dict[str, Any]) -> ToolResult:
            assert self.client is not None
            result = await self.client.call_tool(name, args)
            text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
            return ToolResult(text, bool(result.is_error))

        return call

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        return await self.tools[name].call(args)

    def pick(self, *names: str) -> list[Tool]:
        """Choose a subset of tools by name. A typo raises KeyError instead of silently dropping a tool."""
        return [self.tools[n] for n in names]

    async def get_prompt_text(self, name: str, arguments: dict[str, str]) -> str:
        assert self.client is not None
        prompt = await self.client.get_prompt(name, arguments)
        return "\n".join(m.content.text for m in prompt.messages if getattr(m.content, "type", "") == "text")


def _server_params() -> StdioServerParameters:
    # Pass only BOARDBOT_* variables to the server process: it needs the database path, nothing else.
    env = {k: v for k, v in os.environ.items() if k.startswith("BOARDBOT_")}
    return StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], env=env, cwd=str(ROOT))


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic writes nested models as $defs/$ref. Inline them so a schema is one self-contained object."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def preview(text: str, limit: int = 240) -> str:
    return text[:limit]
