"""Project tools for the Clambake MCP Server.

Exposes project listing and details by querying Clambake's memory tables.
Falls back to Clambake CLI if Postgres is unreachable.
"""

from mcp.server.fastmcp import FastMCP
from ..db import fetch, fetchrow, DatabaseUnavailable, cli_project_list, cli_recall

DEGRADED_MSG = "[CLAMBAKE DEGRADED] Postgres is down — using CLI fallback. Results may be limited."


def register_project_tools(mcp: FastMCP):
    """Register all project tools with the MCP server."""

    @mcp.tool()
    async def project_list(
        status: str | None = None,
    ) -> str:
        """List all known projects with memory counts.

        Args:
            status: Optional filter — only show projects with memories in this status (e.g. "active").
        """
        try:
            sql = """
                SELECT project,
                       COUNT(*) AS total_memories,
                       COUNT(*) FILTER (WHERE status = 'active') AS active_memories,
                       MAX(updated_at) AS last_updated,
                       array_agg(DISTINCT memory_type) AS memory_types
                FROM clambake.project_memory
            """
            params = []
            if status:
                sql += " WHERE status = $1"
                params.append(status)
            sql += " GROUP BY project ORDER BY MAX(updated_at) DESC"

            rows = await fetch(sql, *params)

            if not rows:
                return "No projects found."

            lines = [f"Projects ({len(rows)}):"]
            for r in rows:
                types = ", ".join(sorted(r["memory_types"]))
                lines.append(
                    f"\n  {r['project']}: {r['active_memories']} active / "
                    f"{r['total_memories']} total memories"
                    f"\n    Types: {types}"
                    f"\n    Last updated: {r['last_updated'].isoformat()}"
                )
            return "\n".join(lines)

        except DatabaseUnavailable:
            result = cli_project_list()
            return f"{DEGRADED_MSG}\n{result}"

    @mcp.tool()
    async def project_get(
        project: str,
        include_resolved: bool = False,
        limit: int = 20,
    ) -> str:
        """Get all memories for a specific project.

        Args:
            project: The project name (e.g. "doc-db-v2").
            include_resolved: If true, also return resolved/deprecated memories.
            limit: Max memories to return (default 20).
        """
        try:
            sql = """
                SELECT id, memory_type, title, content, status, tags,
                       related_files, created_at, updated_at
                FROM clambake.project_memory
                WHERE project = $1
            """
            params = [project]
            idx = 2

            if not include_resolved:
                sql += " AND status = 'active'"

            sql += f" ORDER BY updated_at DESC LIMIT ${idx}"
            params.append(limit)

            rows = await fetch(sql, *params)

            if not rows:
                return f"No memories found for project '{project}'."

            lines = [f"Project '{project}' — {len(rows)} memory(ies):"]
            for r in rows:
                tags = " ".join(f"#{t}" for t in (r.get("tags") or []))
                status_str = f" ({r['status']})" if r["status"] != "active" else ""
                files = r.get("related_files") or []

                lines.append(f"\n  #{r['id']} [{r['memory_type']}]{status_str} {tags}")
                lines.append(f"  Title: {r['title']}")
                lines.append(f"  {r['content']}")
                if files:
                    lines.append(f"  Files: {', '.join(files)}")

            return "\n".join(lines)

        except DatabaseUnavailable:
            result = cli_recall(project=project)
            return f"{DEGRADED_MSG}\n{result}"
