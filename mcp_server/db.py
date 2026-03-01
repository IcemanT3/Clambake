"""Async database layer for Clambake MCP Server.

Uses asyncpg for non-blocking Postgres access. Connects to the existing
Clambake schema in the docdb database.
"""

import os
import asyncpg

DB_HOST = os.environ.get("CLAMBAKE_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("CLAMBAKE_DB_PORT", "5433"))
DB_NAME = os.environ.get("CLAMBAKE_DB_NAME", "docdb")
DB_USER = os.environ.get("CLAMBAKE_DB_USER", "postgres")
DB_PASS = os.environ.get("CLAMBAKE_DB_PASS", "postgres")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
    return _pool


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
