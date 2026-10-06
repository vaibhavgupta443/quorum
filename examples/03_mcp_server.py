"""EXAMPLE 3a: the smallest MCP SERVER. One tool, one resource, one prompt.

You don't run this directly; 03_mcp_client.py starts it as a subprocess and talks to it over stdin/stdout.
(You can also plug this exact file into Claude Desktop / Claude Code as an MCP server.)
"""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("demo-server")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
def word_count(text: str) -> dict:
    """Count the words in a piece of text."""  # <- this docstring becomes the tool description the model reads
    return {"words": len(text.split())}


@mcp.resource("demo://greeting")
def greeting() -> str:
    """A static piece of context a client can read."""
    return "Hello from the demo MCP server!"


@mcp.prompt()
def summarize(topic: str) -> str:
    """A reusable instruction template."""
    return f"Write a three-sentence summary of {topic} for a beginner."


if __name__ == "__main__":
    mcp.run()  # speaks the MCP protocol on stdin/stdout
