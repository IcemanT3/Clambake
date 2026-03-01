"""Memory tools for the Clambake MCP Server.

Exposes Clambake's project_memory and global_memory tables as MCP tools
that Claude can call natively during conversations.

Falls back to Clambake CLI if Postgres is unreachable.
"""

import json
from mcp.server.fastmcp import FastMCP
from ..db import (
    fetch, fetchrow, fetchval, execute,
    DatabaseUnavailable, cli_recall, cli_remember,
)

DEGRADED_MSG = "[CLAMBAKE DEGRADED] Postgres is down — using CLI fallback. Results may be limited."


def register_memory_tools(mcp: FastMCP):
    """Register all memory tools with the MCP server."""

    @mcp.tool()
    async def memory_store(
        content: str,
        memory_type: str,
        title: str,
        scope: str = "global",
        project: str | None = None,
        tags: list[str] | None = None,
        related_files: list[str] | None = None,
    ) -> str:
        """Store a new memory. Call this when you learn something worth remembering.

        Args:
            content: The full memory content (a complete thought or fact, not a fragment).
            memory_type: For project scope: architecture, feature, issue, fix, decision, pattern, gotcha, update.
                         For global scope: infrastructure, convention, tool, preference, credential, lesson.
            title: Short descriptive title for the memory.
            scope: "project" or "global". Use project for project-specific knowledge, global for cross-project facts.
            project: Required when scope is "project". The project name (e.g. "doc-db-v2").
            tags: Optional list of tags for categorization.
            related_files: Optional list of file paths this memory relates to (project scope only).
        """
        tag_list = tags or []
        file_list = related_files or []

        try:
            if scope == "project":
                if not project:
                    return "Error: project name is required for project-scoped memories."
                row = await fetchrow(
                    """
                    INSERT INTO clambake.project_memory
                        (project, memory_type, title, content, tags, related_files, created_by)
                    VALUES ($1, $2, $3, $4, $5, $6, 'mcp')
                    RETURNING id
                    """,
                    project, memory_type, title, content, tag_list, file_list,
                )
            else:
                row = await fetchrow(
                    """
                    INSERT INTO clambake.global_memory
                        (memory_type, title, content, tags, created_by)
                    VALUES ($1, $2, $3, $4, 'mcp')
                    RETURNING id
                    """,
                    memory_type, title, content, tag_list,
                )
            mem_id = row["id"]
            return f"Stored {scope} memory #{mem_id}: {title}"

        except DatabaseUnavailable:
            tag_str = ",".join(tag_list) if tag_list else ""
            result = cli_remember(
                project=project, memory_type=memory_type,
                title=title, content=content,
                is_global=(scope == "global"), tags=tag_str,
            )
            return f"{DEGRADED_MSG}\n{result}"

    @mcp.tool()
    async def memory_search(
        query: str,
        scope: str = "all",
        project: str | None = None,
        memory_type: str | None = None,
        tags: list[str] | None = None,
        current_only: bool = True,
        limit: int = 10,
    ) -> str:
        """Search memories by keyword. Returns complete memory records.

        Args:
            query: Search text (matches against title and content).
            scope: "project", "global", or "all" to search both.
            project: Filter to a specific project (only for project/all scope).
            memory_type: Filter by memory type.
            tags: Filter by tags (matches any).
            current_only: If true, only return active memories (default true).
            limit: Maximum results to return (default 10).
        """
        try:
            results = []
            search_pattern = f"%{query}%"

            if scope in ("project", "all"):
                sql = """
                    SELECT id, project, memory_type, title, content, status,
                           tags, related_files, created_at, updated_at
                    FROM clambake.project_memory
                    WHERE (title ILIKE $1 OR content ILIKE $1)
                """
                params = [search_pattern]
                idx = 2

                if current_only:
                    sql += " AND status = 'active'"
                if project:
                    sql += f" AND project = ${idx}"
                    params.append(project)
                    idx += 1
                if memory_type:
                    sql += f" AND memory_type = ${idx}"
                    params.append(memory_type)
                    idx += 1
                if tags:
                    sql += f" AND tags && ${idx}"
                    params.append(tags)
                    idx += 1

                sql += f" ORDER BY updated_at DESC LIMIT ${idx}"
                params.append(limit)

                rows = await fetch(sql, *params)
                for r in rows:
                    results.append(_format_memory(r, scope="project"))

            if scope in ("global", "all"):
                sql = """
                    SELECT id, memory_type, title, content,
                           tags, created_at, updated_at
                    FROM clambake.global_memory
                    WHERE (title ILIKE $1 OR content ILIKE $1)
                """
                params = [search_pattern]
                idx = 2

                if memory_type:
                    sql += f" AND memory_type = ${idx}"
                    params.append(memory_type)
                    idx += 1
                if tags:
                    sql += f" AND tags && ${idx}"
                    params.append(tags)
                    idx += 1

                sql += f" ORDER BY updated_at DESC LIMIT ${idx}"
                params.append(limit)

                rows = await fetch(sql, *params)
                for r in rows:
                    results.append(_format_memory(r, scope="global"))

            if not results:
                return f"No memories found matching '{query}'."

            header = f"Found {len(results)} memory(ies) matching '{query}':\n"
            return header + "\n---\n".join(results)

        except DatabaseUnavailable:
            is_global = scope in ("global", "all")
            result = cli_recall(project=project, search=query, is_global=is_global)
            return f"{DEGRADED_MSG}\n{result}"

    @mcp.tool()
    async def memory_update(
        memory_id: int,
        scope: str,
        content: str | None = None,
        title: str | None = None,
        memory_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
    ) -> str:
        """Update an existing memory. Use when facts change or to mark memories as superseded.

        Args:
            memory_id: The memory ID to update.
            scope: "project" or "global" — which table the memory is in.
            content: New content (optional).
            title: New title (optional).
            memory_type: New type (optional).
            tags: New tags — replaces existing (optional).
            status: New status: active, resolved, deprecated, superseded (project only).
        """
        try:
            table = "clambake.project_memory" if scope == "project" else "clambake.global_memory"

            sets = ["updated_at = NOW()"]
            params = []
            idx = 1

            if content is not None:
                sets.append(f"content = ${idx}")
                params.append(content)
                idx += 1
            if title is not None:
                sets.append(f"title = ${idx}")
                params.append(title)
                idx += 1
            if memory_type is not None:
                sets.append(f"memory_type = ${idx}")
                params.append(memory_type)
                idx += 1
            if tags is not None:
                sets.append(f"tags = ${idx}")
                params.append(tags)
                idx += 1
            if status is not None and scope == "project":
                sets.append(f"status = ${idx}")
                params.append(status)
                idx += 1

            if len(sets) == 1:
                return "Nothing to update — provide at least one field to change."

            params.append(memory_id)
            sql = f"UPDATE {table} SET {', '.join(sets)} WHERE id = ${idx} RETURNING id, title"
            row = await fetchrow(sql, *params)

            if not row:
                return f"Memory #{memory_id} not found in {scope} scope."
            return f"Updated {scope} memory #{row['id']}: {row['title']}"

        except DatabaseUnavailable:
            return f"{DEGRADED_MSG}\nmemory_update requires direct database access. Use `clambake update-memory {memory_id}` from the terminal instead."

    @mcp.tool()
    async def memory_context(
        scope: str = "all",
        project: str | None = None,
        memory_type: str | None = None,
        limit: int = 15,
    ) -> str:
        """Pull a curated set of memories for the current context.

        Call this at the start of a conversation or when switching topics
        to load relevant background knowledge.

        Args:
            scope: "project", "global", or "all".
            project: The project name (required for project scope).
            memory_type: Optional filter by type.
            limit: Max memories to return (default 15, be mindful of context budget).
        """
        try:
            results = []

            if scope in ("project", "all"):
                sql = """
                    SELECT id, project, memory_type, title, content, status,
                           tags, related_files, created_at, updated_at
                    FROM clambake.project_memory
                    WHERE status = 'active'
                """
                params = []
                idx = 1

                if project:
                    sql += f" AND project = ${idx}"
                    params.append(project)
                    idx += 1
                if memory_type:
                    sql += f" AND memory_type = ${idx}"
                    params.append(memory_type)
                    idx += 1

                sql += f" ORDER BY updated_at DESC LIMIT ${idx}"
                params.append(limit)

                rows = await fetch(sql, *params)
                for r in rows:
                    results.append(_format_memory(r, scope="project"))

            if scope in ("global", "all"):
                sql = """
                    SELECT id, memory_type, title, content,
                           tags, created_at, updated_at
                    FROM clambake.global_memory
                """
                params = []
                idx = 1

                if memory_type:
                    sql += f" WHERE memory_type = ${idx}"
                    params.append(memory_type)
                    idx += 1

                sql += f" ORDER BY updated_at DESC LIMIT ${idx}"
                params.append(limit)

                rows = await fetch(sql, *params)
                for r in rows:
                    results.append(_format_memory(r, scope="global"))

            if not results:
                return "No memories found for this context."

            header = f"Context loaded: {len(results)} memory(ies)\n"
            return header + "\n---\n".join(results)

        except DatabaseUnavailable:
            lines = [DEGRADED_MSG]
            if scope in ("global", "all"):
                lines.append(cli_recall(is_global=True))
            if scope in ("project", "all") and project:
                lines.append(cli_recall(project=project))
            return "\n".join(lines)

    @mcp.tool()
    async def memory_delete(
        memory_id: int,
        scope: str,
    ) -> str:
        """Permanently delete a memory. Prefer memory_update with status='deprecated' instead.

        Args:
            memory_id: The memory ID to delete.
            scope: "project" or "global".
        """
        try:
            table = "clambake.project_memory" if scope == "project" else "clambake.global_memory"
            row = await fetchrow(
                f"DELETE FROM {table} WHERE id = $1 RETURNING id, title", memory_id
            )
            if not row:
                return f"Memory #{memory_id} not found in {scope} scope."
            return f"Deleted {scope} memory #{row['id']}: {row['title']}"

        except DatabaseUnavailable:
            return f"{DEGRADED_MSG}\nmemory_delete requires direct database access. Not available in degraded mode."

    @mcp.tool()
    async def memory_checkpoint(
        memories: list[dict],
    ) -> str:
        """Bulk-save conversation takeaways to Postgres before context gets long.

        IMPORTANT: Call this tool proactively when:
        - The conversation is getting long and context compaction may occur soon
        - You've accumulated significant new knowledge that hasn't been saved yet
        - The user is about to switch topics or end a work session
        - You've made important decisions or discoveries worth preserving

        Each memory in the list should be a complete, self-contained fact.
        Do NOT save trivial or ephemeral details — focus on durable knowledge.

        Args:
            memories: A list of memory objects, each with:
                - title (str): Short descriptive title
                - content (str): The full memory (complete thought, not a fragment)
                - memory_type (str): Type of memory (see memory_store for valid types)
                - scope (str): "project" or "global"
                - project (str, optional): Required if scope is "project"
                - tags (list[str], optional): Tags for categorization
                - related_files (list[str], optional): Related file paths

        Example:
            memory_checkpoint(memories=[
                {"title": "User prefers dark mode", "content": "Greg prefers dark mode in all UIs...", "memory_type": "preference", "scope": "global"},
                {"title": "Doc DB uses port 8501", "content": "Doc DB v2 runs on port 8501...", "memory_type": "architecture", "scope": "project", "project": "doc-db-v2"}
            ])
        """
        if not memories:
            return "No memories provided to checkpoint."

        saved = []
        errors = []
        degraded = False

        for i, mem in enumerate(memories):
            title = mem.get("title", "").strip()
            content = mem.get("content", "").strip()
            memory_type = mem.get("memory_type", "").strip()
            scope = mem.get("scope", "global").strip()
            project = mem.get("project", "").strip() or None
            tag_list = mem.get("tags") or []
            file_list = mem.get("related_files") or []

            if not title or not content or not memory_type:
                errors.append(f"[{i}] Skipped — missing title, content, or memory_type.")
                continue

            try:
                if scope == "project":
                    if not project:
                        errors.append(f"[{i}] '{title}' — project name required for project scope.")
                        continue
                    row = await fetchrow(
                        """
                        INSERT INTO clambake.project_memory
                            (project, memory_type, title, content, tags, related_files, created_by)
                        VALUES ($1, $2, $3, $4, $5, $6, 'mcp-checkpoint')
                        RETURNING id
                        """,
                        project, memory_type, title, content, tag_list, file_list,
                    )
                else:
                    row = await fetchrow(
                        """
                        INSERT INTO clambake.global_memory
                            (memory_type, title, content, tags, created_by)
                        VALUES ($1, $2, $3, $4, 'mcp-checkpoint')
                        RETURNING id
                        """,
                        memory_type, title, content, tag_list,
                    )
                saved.append(f"#{row['id']} [{scope}] {title}")

            except DatabaseUnavailable:
                degraded = True
                tag_str = ",".join(tag_list) if tag_list else ""
                result = cli_remember(
                    project=project, memory_type=memory_type,
                    title=title, content=content,
                    is_global=(scope == "global"), tags=tag_str,
                )
                saved.append(f"[cli] [{scope}] {title} — {result}")

            except Exception as e:
                errors.append(f"[{i}] '{title}' — {str(e)}")

        lines = []
        if degraded:
            lines.append(DEGRADED_MSG)
        lines.append(f"Checkpoint complete: {len(saved)} saved, {len(errors)} skipped.")
        if saved:
            lines.append("\nSaved:")
            for s in saved:
                lines.append(f"  {s}")
        if errors:
            lines.append("\nErrors:")
            for e in errors:
                lines.append(f"  {e}")

        return "\n".join(lines)


def _format_memory(record, scope: str) -> str:
    """Format a memory record as readable text."""
    tags = " ".join(f"#{t}" for t in (record.get("tags") or []))
    project = record.get("project", "")
    status = record.get("status", "")
    files = record.get("related_files") or []

    header = f"[{scope}] #{record['id']} [{record['memory_type']}]"
    if project:
        header += f" project={project}"
    if status and status != "active":
        header += f" ({status})"
    if tags:
        header += f" {tags}"

    lines = [header, f"Title: {record['title']}", record["content"]]

    if files:
        lines.append(f"Files: {', '.join(files)}")

    updated = record.get("updated_at")
    if updated:
        lines.append(f"Updated: {updated.isoformat()}")

    return "\n".join(lines)
