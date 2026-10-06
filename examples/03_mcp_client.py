"""EXAMPLE 3b: an MCP CLIENT. Needs NO API key: it just shows what the protocol does.

Run:  python examples/03_mcp_client.py

Notice the client has no idea what tools exist until it ASKS. That is MCP's whole point: a server describes its tools
once; any client (this script, our agent, Claude Desktop) can discover and use them.
In the real app, the client then hands those descriptions to the model, and the model decides when to call them.
"""

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

SERVER = Path(__file__).with_name("03_mcp_server.py")


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])  # how to start the server
    async with Client(params) as client:
        tools = await client.list_tools()  # 1. DISCOVER
        for tool in tools.tools:
            print("tool:", tool.name, "|", tool.description, "| read-only:", tool.annotations.read_only_hint)
            print("      schema:", tool.input_schema)

        result = await client.call_tool("word_count", {"text": "agents are just loops with tools"})  # 2. CALL
        print("call result:", result.content[0].text)

        print("resource:", (await client.read_resource("demo://greeting")).contents[0].text)  # 3. READ CONTEXT
        print("prompt:", (await client.get_prompt("summarize", {"topic": "MCP"})).messages[0].content.text)  # 4. TEMPLATES


asyncio.run(main())
