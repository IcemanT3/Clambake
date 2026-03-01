"""Clambake MCP Server — entry point.

Exposes Clambake's memory system via Model Context Protocol so that
Claude Desktop and Claude Code can natively read/write memories
during conversations.

Usage:
    python -m mcp_server.server
"""

from mcp.server.fastmcp import FastMCP
from .tools.memory import register_memory_tools
from .tools.projects import register_project_tools
from .db import close_pool

mcp = FastMCP(
    "clambake-memory",
    instructions=(
        "Persistent memory system for Claude. "
        "Store and retrieve knowledge about the user, projects, "
        "infrastructure, and preferences across conversations. "
        "Call memory_checkpoint proactively when conversations get long "
        "to preserve important knowledge before context compaction."
    ),
)

# Register tool groups
register_memory_tools(mcp)
register_project_tools(mcp)


def main():
    """Run the MCP server with stdio transport."""
    try:
        mcp.run(transport="stdio")
    finally:
        import asyncio
        asyncio.run(close_pool())


if __name__ == "__main__":
    main()
