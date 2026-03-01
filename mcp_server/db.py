"""Async database layer for Clambake MCP Server.

Uses asyncpg for non-blocking Postgres access. Connects to the existing
Clambake schema in the docdb database.

Falls back to Clambake CLI if Postgres is unreachable.
"""

import asyncio
import os
import subprocess
import asyncpg

DB_HOST = os.environ.get("CLAMBAKE_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("CLAMBAKE_DB_PORT", "5433"))
DB_NAME = os.environ.get("CLAMBAKE_DB_NAME", "docdb")
DB_USER = os.environ.get("CLAMBAKE_DB_USER", "postgres")
DB_PASS = os.environ.get("CLAMBAKE_DB_PASS", "postgres")

CLAMBAKE_CLI = os.environ.get("CLAMBAKE_CLI", "clambake")

_pool: asyncpg.Pool | None = None
_db_available: bool | None = None  # None = unknown, True/False = tested


class DatabaseUnavailable(Exception):
    """Raised when Postgres is unreachable."""
    pass


async def get_pool() -> asyncpg.Pool:
    """Get or create the connection pool. Raises DatabaseUnavailable if Postgres is down."""
    global _pool, _db_available
    if _pool is not None:
        return _pool
    try:
        _pool = await asyncio.wait_for(
            asyncpg.create_pool(
                host=DB_HOST,
                port=DB_PORT,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASS,
                min_size=1,
                max_size=5,
                command_timeout=30,
            ),
            timeout=5,
        )
        _db_available = True
        return _pool
    except Exception:
        _db_available = False
        _pool = None
        raise DatabaseUnavailable("Postgres is unreachable")


def is_db_available() -> bool | None:
    """Check cached database availability. None if never tested."""
    return _db_available


async def reset_pool():
    """Reset pool so next call retries the connection."""
    global _pool, _db_available
    if _pool:
        try:
            await _pool.close()
        except Exception:
            pass
    _pool = None
    _db_available = None


async def close_pool():
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def execute(query: str, *args):
    """Execute a query and return status."""
    pool = await get_pool()
    return await pool.execute(query, *args)


async def fetch(query: str, *args) -> list[asyncpg.Record]:
    """Fetch multiple rows."""
    pool = await get_pool()
    return await pool.fetch(query, *args)


async def fetchrow(query: str, *args) -> asyncpg.Record | None:
    """Fetch a single row."""
    pool = await get_pool()
    return await pool.fetchrow(query, *args)


async def fetchval(query: str, *args):
    """Fetch a single value."""
    pool = await get_pool()
    return await pool.fetchval(query, *args)


# --- CLI Fallback -----------------------------------------------------------

def cli_recall(project: str | None = None, search: str | None = None, is_global: bool = False) -> str:
    """Fall back to `clambake recall` CLI when Postgres is down."""
    cmd = [CLAMBAKE_CLI, "recall"]
    if is_global:
        cmd.append("--global")
    elif project:
        cmd.extend(["--project", project])
    if search:
        cmd.extend(["--search", search])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() or result.stderr.strip() or "No results"
    except Exception as e:
        return f"CLI fallback also failed: {e}"


def cli_remember(project: str | None, memory_type: str, title: str, content: str,
                 is_global: bool = False, tags: str = "") -> str:
    """Fall back to `clambake remember` CLI when Postgres is down."""
    cmd = [CLAMBAKE_CLI, "remember"]
    if is_global:
        cmd.append("--global")
    elif project:
        cmd.extend(["--project", project])
    cmd.extend(["--type", memory_type, "--title", title, "--content", content])
    if tags:
        cmd.extend(["--tags", tags])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() or result.stderr.strip() or "Stored via CLI"
    except Exception as e:
        return f"CLI fallback also failed: {e}"


def cli_project_list() -> str:
    """Fall back to `clambake project-list` CLI."""
    try:
        result = subprocess.run(
            [CLAMBAKE_CLI, "project-list"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() or "No projects found"
    except Exception as e:
        return f"CLI fallback also failed: {e}"
