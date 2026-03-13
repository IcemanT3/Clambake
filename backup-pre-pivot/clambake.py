#!/usr/bin/env python3
"""
Clambake — Multi-Instance Claude Code Coordination via Postgres

A lightweight CLI that Claude Code instances use to coordinate work
across projects through a shared Postgres database.

Usage:
    clambake up [--project <name>] [--dir <path>]  # One-command startup (register + inbox + recall)
    clambake down                                    # One-command shutdown (deregister + optional summary)
    clambake status                                  # Show active instances + recent messages
    clambake infra                                   # Show live infrastructure status
    clambake infra-warn --service <name> --status <s> --message <text>
    clambake remember --project <name> --type <type> --title <text> --content <text>
    clambake recall --project <name> [--search <query>]
    clambake recall --global [--search <query>]
    clambake send --to <target> --subject <text> [--body <text>] [--type <type>]
    clambake inbox [--all]
    clambake project-list                            # List all known projects with memory counts
    clambake init                                    # Initialize schema
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import subprocess
import time

import psycopg2
import psycopg2.extras
import requests

# --- Configuration -----------------------------------------------------------

# Master switch: defaults to ENABLED. Set CLAMBAKE_ENABLED=0 to disable.
# When disabled, all commands silently exit 0 (no output, no errors).
# The 'enable', 'disable', and 'init' commands always run regardless.
CLAMBAKE_ENABLED = os.environ.get("CLAMBAKE_ENABLED", "1") == "1"
CLAMBAKE_FLAG_FILE = Path(os.environ.get(
    "CLAMBAKE_FLAG_FILE",
    Path.home() / ".clambake_enabled"
))

# Also check flag file (overrides env if present)
if CLAMBAKE_FLAG_FILE.exists():
    CLAMBAKE_ENABLED = CLAMBAKE_FLAG_FILE.read_text().strip() == "1"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.environ.get("CLAMBAKE_EMBEDDING_MODEL", "nomic-embed-text")

DB_HOST = os.environ.get("CLAMBAKE_DB_HOST", "localhost")
DB_PORT = os.environ.get("CLAMBAKE_DB_PORT", "5433")
DB_NAME = os.environ.get("CLAMBAKE_DB_NAME", "docdb")
DB_USER = os.environ.get("CLAMBAKE_DB_USER", "postgres")
DB_PASS = os.environ.get("CLAMBAKE_DB_PASS", "postgres")

# Instance ID persists for the session, stored in a per-session temp file.
# CLAUDE_SESSION_ID makes each Claude Code session get its own file;
# falls back to a single global file if the env var isn't set.
_session_id = os.environ.get("CLAUDE_SESSION_ID", "")
if _session_id:
    _default_instance_file = Path.home() / f".clambake_instance_{_session_id}"
else:
    _default_instance_file = Path.home() / ".clambake_instance"
INSTANCE_FILE = Path(os.environ.get(
    "CLAMBAKE_INSTANCE_FILE",
    _default_instance_file
))

# --- Project detection -------------------------------------------------------

# Optional overrides: directory prefix -> project name.
# Only needed when the folder name doesn't match the desired project name.
# Example: "F:/some/nested/path": "my-project"
PROJECT_OVERRIDES = {}


def _normalize_name(name):
    """Normalize a directory name into a project slug (lowercase, hyphens)."""
    return name.strip().lower().replace(" ", "-")


def detect_project(working_dir=None):
    """Auto-detect project name from working directory.

    1. Check PROJECT_OVERRIDES for explicit mappings.
    2. Otherwise derive from the directory name (lowercase, spaces to hyphens).
    """
    d = (working_dir or os.getcwd()).replace("\\", "/")
    # Check explicit overrides first
    for prefix, project in PROJECT_OVERRIDES.items():
        if d.startswith(prefix):
            return project
    # Default: derive from directory name
    name = Path(d).name
    if not name:
        # Drive root (e.g. F:/) — use drive letter
        drive = Path(d).anchor.rstrip(":/\\")
        return drive.lower() + "-drive" if drive else "unknown"
    return _normalize_name(name)


def get_conn():
    """Get a database connection."""
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS
    )


def _docker_ready():
    """Check if Docker daemon is responding."""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _try_start_postgres():
    """Attempt to start Docker and the Postgres container. Returns True if started."""
    try:
        if not _docker_ready():
            # Docker not running — start backend without GUI
            subprocess.Popen(
                [r"C:\Program Files\Docker\Docker\resources\com.docker.backend.exe",
                 "-with-frontend=false"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            # Wait for Docker daemon (up to 60s — cold start can be slow)
            for _ in range(20):
                time.sleep(3)
                if _docker_ready():
                    break
            else:
                return False

        # Docker is running — start Postgres container (retry if engine not fully ready)
        for _ in range(3):
            result = subprocess.run(
                ["docker", "start", "postgres"], capture_output=True, timeout=15
            )
            if result.returncode == 0:
                break
            time.sleep(3)
        else:
            return False

        # Wait for Postgres to accept connections (up to 30s)
        for _ in range(15):
            time.sleep(2)
            try:
                conn = get_conn()
                conn.close()
                return True
            except (psycopg2.OperationalError, psycopg2.Error):
                continue
        return False
    except Exception:
        return False


def get_conn_safe():
    """Get a database connection. Auto-starts Docker/Postgres if needed."""
    try:
        return get_conn()
    except (psycopg2.OperationalError, psycopg2.Error):
        pass

    # Postgres unreachable — try to start it
    if _try_start_postgres():
        try:
            return get_conn()
        except (psycopg2.OperationalError, psycopg2.Error):
            return None
    return None


def get_instance_id():
    """Read current instance ID from file, or None."""
    if INSTANCE_FILE.exists():
        data = json.loads(INSTANCE_FILE.read_text())
        return data.get("instance_id"), data.get("project")
    return None, None


def save_instance_id(instance_id, project, role=None):
    """Save instance ID to file."""
    data = {"instance_id": instance_id, "project": project}
    if role:
        data["role"] = role
    INSTANCE_FILE.write_text(json.dumps(data))


def clear_instance_id():
    """Remove instance ID file."""
    if INSTANCE_FILE.exists():
        INSTANCE_FILE.unlink()


# --- Embedding ---------------------------------------------------------------

def generate_embedding(text, prefix=""):
    """Generate a 768-dim embedding via Ollama. Returns list or [] on failure."""
    try:
        resp = requests.post(
            "%s/api/embed" % OLLAMA_BASE_URL,
            json={"model": EMBEDDING_MODEL, "input": prefix + text},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama returns {"embeddings": [[...]]}
        embeddings = data.get("embeddings")
        if embeddings and len(embeddings) > 0:
            return embeddings[0]
        return []
    except Exception:
        return []


# --- Commands ----------------------------------------------------------------

def cmd_init(args):
    """Initialize the clambake schema in Postgres."""
    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        print("ERROR: schema.sql not found next to clambake.py")
        sys.exit(1)

    sql = schema_path.read_text()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print("OK: Clambake schema initialized in database '%s'" % DB_NAME)
    finally:
        conn.close()


def cmd_register(args):
    """Register this instance as active."""
    instance_id = str(uuid.uuid4())[:12]
    project = args.project
    working_dir = args.dir or os.getcwd()
    model = args.model or "unknown"
    role = getattr(args, "role", None) or os.environ.get("CLAMBAKE_ROLE")

    conn = get_conn()
    try:
        # Auto-cleanup stale data before registering
        counts = _run_cleanup(conn)
        cleaned = sum(v for v in counts.values() if isinstance(v, int))
        if cleaned > 0:
            parts = []
            if counts.get("stale_instances"):
                parts.append("%d stale instance(s)" % counts["stale_instances"])
            if counts.get("orphaned_tasks"):
                parts.append("%d orphaned task(s)" % counts["orphaned_tasks"])
            if counts.get("expired_messages"):
                parts.append("%d expired msg(s)" % counts["expired_messages"])
            if parts:
                print("AUTO-CLEANUP: %s" % ", ".join(parts))

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.instances
                    (instance_id, project, working_dir, model, role, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
                ON CONFLICT (instance_id) DO UPDATE SET
                    last_heartbeat = NOW(), status = 'active', role = EXCLUDED.role
            """, (instance_id, project, working_dir, model, role))
        conn.commit()
        save_instance_id(instance_id, project, role)
        role_tag = " <%s>" % role if role else ""
        print("REGISTERED: %s%s on project '%s'" % (instance_id, role_tag, project))

        # Check for other active instances and unread messages
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT instance_id, project, current_task, role, status
                FROM clambake.active_instances
                WHERE instance_id != %s
            """, (instance_id,))
            others = cur.fetchall()
            if others:
                print("\nACTIVE INSTANCES:")
                for o in others:
                    task = o["current_task"] or "idle"
                    role_tag = " <%s>" % o["role"] if o.get("role") else ""
                    print("  [%s]%s %s — %s (%s)" % (
                        o["status"], role_tag, o["project"], task, o["instance_id"]))

            # Check for messages to this project or @all
            cur.execute("""
                SELECT COUNT(*) as cnt FROM clambake.unread_messages
                WHERE to_target IN (%s, %s, '@all')
            """, (instance_id, project))
            msg_count = cur.fetchone()["cnt"]
            if msg_count:
                print("\n%d UNREAD MESSAGE(S) — run 'clambake inbox'" % msg_count)

            # Auto-recall: load core memories for this project
            project_embedding = generate_embedding(project, prefix="search_query: ")
            if project_embedding:
                cur.execute("""
                    SELECT id, memory_type, title, content,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM clambake.project_memory
                    WHERE project = %s AND status = 'active'
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT 5
                """, (project_embedding, project, project_embedding))
            else:
                # Fallback: most recent memories
                cur.execute("""
                    SELECT id, memory_type, title, content, NULL::float AS similarity
                    FROM clambake.project_memory
                    WHERE project = %s AND status = 'active'
                    ORDER BY updated_at DESC
                    LIMIT 5
                """, (project,))
            memories = cur.fetchall()
            if memories:
                print("\nCORE MEMORIES:")
                for m in memories:
                    sim = m.get("similarity")
                    sim_str = " [%.2f]" % sim if sim is not None else ""
                    preview = m["content"][:200]
                    if len(m["content"]) > 200:
                        preview += "..."
                    print("  #%d [%s]%s %s" % (
                        m["id"], m["memory_type"], sim_str, m["title"]))
                    print("    %s" % preview)
    finally:
        conn.close()


def cmd_heartbeat(args):
    """Update heartbeat and optionally current task/status/role."""
    instance_id, project = get_instance_id()
    if not instance_id:
        print("ERROR: Not registered. Run 'clambake register' first.")
        sys.exit(1)

    role = getattr(args, "role", None) or os.environ.get("CLAMBAKE_ROLE")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            updates = ["last_heartbeat = NOW()"]
            params = []
            if args.task:
                updates.append("current_task = %s")
                params.append(args.task)
            if args.status:
                updates.append("status = %s")
                params.append(args.status)
            if role:
                updates.append("role = %s")
                params.append(role)
            params.append(instance_id)

            cur.execute(
                "UPDATE clambake.instances SET %s WHERE instance_id = %%s"
                % ", ".join(updates), params
            )
        conn.commit()
        task_msg = " task='%s'" % args.task if args.task else ""
        role_msg = " role=%s" % role if role else ""
        print("HEARTBEAT: %s%s%s" % (instance_id, role_msg, task_msg))
    finally:
        conn.close()


def cmd_checkin(args):
    """Lightweight check-in: heartbeat if registered, re-register if not.

    Designed to run on every user prompt via the UserPromptSubmit hook.
    Fast path: just updates last_heartbeat. If the instance was cleaned up
    (e.g., after reboot or stale cleanup), re-registers automatically.
    """
    conn = get_conn_safe()
    if not conn:
        return  # Postgres down, skip silently

    try:
        instance_id, project = get_instance_id()

        if instance_id:
            # Fast path: update heartbeat, check if still in DB
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE clambake.instances
                    SET last_heartbeat = NOW(), status = 'active'
                    WHERE instance_id = %s
                    RETURNING instance_id
                """, (instance_id,))
                row = cur.fetchone()

                if row:
                    # Still registered — just heartbeat
                    conn.commit()
                    print("CHECKIN: %s (%s)" % (instance_id, project))
                    return
                else:
                    # Instance was cleaned up — re-register
                    working_dir = os.getcwd()
                    cur.execute("""
                        INSERT INTO clambake.instances
                            (instance_id, project, working_dir, status)
                        VALUES (%s, %s, %s, 'active')
                        ON CONFLICT (instance_id) DO UPDATE SET
                            last_heartbeat = NOW(), status = 'active'
                    """, (instance_id, project, working_dir))
                    cur.execute("""
                        INSERT INTO clambake.session_log
                            (instance_id, project, action, summary)
                        VALUES (%s, %s, 'started', 'Re-registered after cleanup/reboot')
                    """, (instance_id, project))
                    conn.commit()
                    print("CHECKIN: %s re-registered (%s)" % (instance_id, project))
                    return

        # No instance file — first prompt of a new session, do full registration
        working_dir = os.getcwd()
        project = detect_project(working_dir)
        instance_id = str(uuid.uuid4())[:12]

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.instances
                    (instance_id, project, working_dir, status)
                VALUES (%s, %s, %s, 'active')
                ON CONFLICT (instance_id) DO UPDATE SET
                    last_heartbeat = NOW(), status = 'active'
            """, (instance_id, project, working_dir))
            cur.execute("""
                INSERT INTO clambake.session_log
                    (instance_id, project, action, summary)
                VALUES (%s, %s, 'started', 'Session started (auto-checkin)')
            """, (instance_id, project))
        conn.commit()
        save_instance_id(instance_id, project)
        print("CHECKIN: %s registered (%s)" % (instance_id, project))
    finally:
        conn.close()


def cmd_status(args):
    """Show all active instances and recent messages."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Active instances
            cur.execute("SELECT * FROM clambake.active_instances")
            instances = cur.fetchall()

            print("=== ACTIVE INSTANCES ===")
            if not instances:
                print("  (none)")
            for i in instances:
                task = i["current_task"] or "idle"
                age = i["seconds_since_heartbeat"]
                role_tag = " <%s>" % i["role"] if i.get("role") else ""
                age_str = "%ds ago" % age if age < 60 else "%dm ago" % (age // 60)
                print("  [%s]%s %s — %s (%s) %s" % (
                    i["status"], role_tag, i["project"], task, age_str, i["instance_id"]))

            # Recent messages (last 24h)
            cur.execute("""
                SELECT id, from_instance, from_project, to_target,
                       message_type, subject, is_read, created_at
                FROM clambake.messages
                WHERE created_at > NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC LIMIT 20
            """)
            messages = cur.fetchall()

            print("\n=== RECENT MESSAGES (24h) ===")
            if not messages:
                print("  (none)")
            for m in messages:
                read_mark = " " if m["is_read"] else "*"
                proj = m["from_project"] or "?"
                print("  %s[%d] %s (%s) -> %s: [%s] %s" % (
                    read_mark, m["id"], proj, m["from_instance"][:8],
                    m["to_target"], m["message_type"], m["subject"]))

            # Recent activity
            cur.execute("""
                SELECT project, action, summary, created_at
                FROM clambake.recent_activity LIMIT 10
            """)
            activity = cur.fetchall()

            print("\n=== RECENT ACTIVITY ===")
            if not activity:
                print("  (none)")
            for a in activity:
                ts = a["created_at"].strftime("%m/%d %H:%M")
                print("  %s [%s] %s — %s" % (
                    ts, a["project"], a["action"], a["summary"]))
    finally:
        conn.close()


def cmd_send(args):
    """Send a message to another instance, project, or @all."""
    instance_id, project = get_instance_id()
    if not instance_id:
        print("ERROR: Not registered.")
        sys.exit(1)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.messages
                    (from_instance, from_project, to_target, message_type, subject, body)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (instance_id, project, args.to, args.type, args.subject, args.body))
            msg_id = cur.fetchone()[0]
        conn.commit()
        print("SENT: [%s] #%d to %s — %s" % (args.type, msg_id, args.to, args.subject))
    finally:
        conn.close()


def cmd_inbox(args):
    """Check unread messages for this instance."""
    instance_id, project = get_instance_id()
    if not instance_id:
        print("ERROR: Not registered.")
        sys.exit(1)

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if args.all:
                where = "WHERE (to_target IN (%s, %s, '@all'))"
            else:
                where = "WHERE (to_target IN (%s, %s, '@all')) AND NOT is_read"

            cur.execute("""
                SELECT id, from_instance, from_project, to_target,
                       message_type, subject, body, is_read, created_at
                FROM clambake.messages
                """ + where + """
                AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY created_at DESC LIMIT 50
            """, (instance_id, project))
            messages = cur.fetchall()

            if not messages:
                print("INBOX: empty")
                return

            print("INBOX: %d message(s)" % len(messages))
            for m in messages:
                read_mark = " " if m["is_read"] else "*"
                proj = m["from_project"] or "?"
                ts = m["created_at"].strftime("%m/%d %H:%M")
                print("  %s#%d [%s] %s from %s (%s) — %s" % (
                    read_mark, m["id"], m["message_type"],
                    ts, proj, m["from_instance"][:8], m["subject"]))
                if m["body"]:
                    # Show first 200 chars of body
                    body_preview = m["body"][:200]
                    if len(m["body"]) > 200:
                        body_preview += "..."
                    print("    %s" % body_preview)
    finally:
        conn.close()


def cmd_read(args):
    """Mark a message as read and show full content."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE clambake.messages SET is_read = TRUE
                WHERE id = %s RETURNING *
            """, (args.message_id,))
            m = cur.fetchone()
            if not m:
                print("ERROR: Message #%s not found" % args.message_id)
                sys.exit(1)
        conn.commit()

        print("MESSAGE #%d" % m["id"])
        print("  From: %s (%s)" % (m["from_project"] or "?", m["from_instance"]))
        print("  To: %s" % m["to_target"])
        print("  Type: %s" % m["message_type"])
        print("  Subject: %s" % m["subject"])
        print("  Date: %s" % m["created_at"])
        if m["body"]:
            print("  Body:\n%s" % m["body"])
    finally:
        conn.close()


def cmd_remember(args):
    """Store knowledge in project or global memory."""
    instance_id, _ = get_instance_id()
    created_by = instance_id or "human"
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    files = [f.strip() for f in args.files.split(",")] if args.files else []

    # Generate embedding from title + content
    embed_text = args.title + "\n" + args.content
    embedding = generate_embedding(embed_text)
    embed_status = "(embedded)" if embedding else "(text only)"

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if args.glob:
                # Global memory
                cur.execute("""
                    INSERT INTO clambake.global_memory
                        (memory_type, title, content, tags, created_by, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (args.type, args.title, args.content, tags, created_by,
                      embedding if embedding else None))
            else:
                # Project memory
                cur.execute("""
                    INSERT INTO clambake.project_memory
                        (project, memory_type, title, content, tags,
                         related_files, created_by, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (args.project, args.type, args.title, args.content,
                      tags, files, created_by,
                      embedding if embedding else None))
            mem_id = cur.fetchone()[0]
            # Log activity to session_log
            if instance_id:
                scope_name = "global" if args.glob else args.project
                cur.execute("""
                    INSERT INTO clambake.session_log
                        (instance_id, project, action, summary)
                    VALUES (%s, %s, 'task_completed',
                            %s)
                """, (instance_id, scope_name or "",
                      "Stored %s memory #%d: %s" % (args.type, mem_id, args.title)))
        conn.commit()
        scope = "global" if args.glob else args.project
        print("REMEMBERED: #%d [%s] in %s — %s %s" % (
            mem_id, args.type, scope, args.title, embed_status))
    finally:
        conn.close()


def cmd_recall(args):
    """Query project or global memory."""
    use_semantic = args.search and not args.text_only
    query_embedding = []
    if use_semantic:
        query_embedding = generate_embedding(args.search, prefix="search_query: ")
        if not query_embedding:
            use_semantic = False  # Fallback to text search

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if use_semantic:
                # Semantic search with cosine similarity
                if args.glob:
                    query = """
                        SELECT *, 1 - (embedding <=> %s::vector) AS similarity
                        FROM clambake.global_memory
                        WHERE embedding IS NOT NULL
                    """
                    params = [query_embedding]
                    if args.type:
                        query += " AND memory_type = %s"
                        params.append(args.type)
                    query += " ORDER BY embedding <=> %s::vector LIMIT %s"
                    params.extend([query_embedding, args.limit])
                else:
                    query = """
                        SELECT *, 1 - (embedding <=> %s::vector) AS similarity
                        FROM clambake.project_memory
                        WHERE project = %s AND status = 'active'
                          AND embedding IS NOT NULL
                    """
                    params = [query_embedding, args.project]
                    if args.type:
                        query += " AND memory_type = %s"
                        params.append(args.type)
                    query += " ORDER BY embedding <=> %s::vector LIMIT %s"
                    params.extend([query_embedding, args.limit])
                cur.execute(query, params)
                semantic_rows = cur.fetchall()

                # Also get text-fallback results for entries without embeddings
                if args.glob:
                    fallback_query = """
                        SELECT *, NULL::float AS similarity
                        FROM clambake.global_memory
                        WHERE embedding IS NULL
                          AND (title ILIKE %s OR content ILIKE %s)
                    """
                    fallback_params = ["%%%s%%" % args.search] * 2
                    if args.type:
                        fallback_query += " AND memory_type = %s"
                        fallback_params.append(args.type)
                    fallback_query += " ORDER BY updated_at DESC LIMIT %s"
                    fallback_params.append(args.limit)
                else:
                    fallback_query = """
                        SELECT *, NULL::float AS similarity
                        FROM clambake.project_memory
                        WHERE project = %s AND status = 'active'
                          AND embedding IS NULL
                          AND (title ILIKE %s OR content ILIKE %s)
                    """
                    fallback_params = [args.project] + ["%%%s%%" % args.search] * 2
                    if args.type:
                        fallback_query += " AND memory_type = %s"
                        fallback_params.append(args.type)
                    fallback_query += " ORDER BY updated_at DESC LIMIT %s"
                    fallback_params.append(args.limit)
                cur.execute(fallback_query, fallback_params)
                fallback_rows = cur.fetchall()

                rows = semantic_rows + fallback_rows
            else:
                # Text-only search (original behavior)
                if args.glob:
                    query = "SELECT *, NULL::float AS similarity FROM clambake.global_memory WHERE TRUE"
                    params = []
                    if args.type:
                        query += " AND memory_type = %s"
                        params.append(args.type)
                    if args.search:
                        query += " AND (title ILIKE %s OR content ILIKE %s)"
                        params.extend(["%%%s%%" % args.search] * 2)
                    query += " ORDER BY updated_at DESC LIMIT %s"
                    params.append(args.limit)
                    cur.execute(query, params)
                else:
                    query = """
                        SELECT *, NULL::float AS similarity FROM clambake.project_memory
                        WHERE project = %s AND status = 'active'
                    """
                    params = [args.project]
                    if args.type:
                        query += " AND memory_type = %s"
                        params.append(args.type)
                    if args.search:
                        query += " AND (title ILIKE %s OR content ILIKE %s)"
                        params.extend(["%%%s%%" % args.search] * 2)
                    query += " ORDER BY updated_at DESC LIMIT %s"
                    params.append(args.limit)
                    cur.execute(query, params)

                rows = cur.fetchall()

            if not rows:
                print("RECALL: no results")
                return

            scope = "GLOBAL" if args.glob else args.project.upper()
            mode = "semantic" if use_semantic else "text"
            print("RECALL [%s] (%s): %d result(s)" % (scope, mode, len(rows)))
            for r in rows:
                tags_str = " ".join("#%s" % t for t in (r.get("tags") or []))
                status = r.get("status", "")
                status_str = " (%s)" % status if status and status != "active" else ""
                sim = r.get("similarity")
                sim_str = " [%.2f]" % sim if sim is not None else ""
                print("\n  #%d [%s]%s%s %s %s" % (
                    r["id"], r["memory_type"], status_str, sim_str, r["title"], tags_str))
                # Show first 300 chars of content
                content = r["content"][:300]
                if len(r["content"]) > 300:
                    content += "..."
                print("    %s" % content)

                if r.get("related_files"):
                    print("    files: %s" % ", ".join(r["related_files"]))
    finally:
        conn.close()


def cmd_log(args):
    """Log a session action."""
    instance_id, project = get_instance_id()
    if not instance_id:
        print("ERROR: Not registered.")
        sys.exit(1)

    files = [f.strip() for f in args.files.split(",")] if args.files else []

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.session_log
                    (instance_id, project, action, summary, files_modified)
                VALUES (%s, %s, %s, %s, %s)
            """, (instance_id, project, args.action, args.summary, files))
        conn.commit()
        print("LOGGED: [%s] %s" % (args.action, args.summary))
    finally:
        conn.close()


def cmd_deregister(args):
    """Mark this instance as gone."""
    instance_id, project = get_instance_id()
    if not instance_id:
        print("Not registered.")
        return

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Log shutdown
            cur.execute("""
                INSERT INTO clambake.session_log
                    (instance_id, project, action, summary)
                VALUES (%s, %s, 'shutdown', 'Session ended')
            """, (instance_id, project))
            # Remove instance
            cur.execute(
                "DELETE FROM clambake.instances WHERE instance_id = %s",
                (instance_id,)
            )
        conn.commit()
        clear_instance_id()
        print("DEREGISTERED: %s" % instance_id)
    finally:
        conn.close()


def _run_cleanup(conn):
    """Run cleanup and return counts dict. Caller must commit."""
    with conn.cursor() as cur:
        cur.execute("SELECT clambake.cleanup()")
        result = cur.fetchone()[0]
    conn.commit()
    if isinstance(result, str):
        return json.loads(result)
    return result or {}


def cmd_cleanup(args):
    """Run cleanup to remove stale data."""
    conn = get_conn()
    try:
        counts = _run_cleanup(conn)
        print("CLEANUP:")
        print("  Stale instances removed: %s" % counts.get("stale_instances", 0))
        print("  Expired messages removed: %s" % counts.get("expired_messages", 0))
        print("  Old logs removed: %s" % counts.get("old_logs", 0))
        print("  Orphaned tasks released: %s" % counts.get("orphaned_tasks", 0))
    finally:
        conn.close()


def cmd_enable(args):
    """Enable Clambake coordination."""
    CLAMBAKE_FLAG_FILE.write_text("1")
    print("ENABLED: Clambake is now active")
    print("  Flag file: %s" % CLAMBAKE_FLAG_FILE)
    print("  Or set env: export CLAMBAKE_ENABLED=1")


def cmd_disable(args):
    """Disable Clambake coordination."""
    CLAMBAKE_FLAG_FILE.write_text("0")
    # Also clean up instance registration
    instance_id, project = get_instance_id()
    if instance_id:
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM clambake.instances WHERE instance_id = %s",
                    (instance_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass
        clear_instance_id()
    print("DISABLED: Clambake is now inactive")
    print("  All commands will silently no-op until re-enabled")
    print("  Re-enable with: clambake enable")


def cmd_role_list(args):
    """List all agent roles."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT name, description, capabilities, tool_allowlist, tool_denylist
                FROM clambake.agent_roles ORDER BY name
            """)
            roles = cur.fetchall()
            if not roles:
                print("ROLES: none defined. Run 'clambake role-seed' to create defaults.")
                return
            print("=== AGENT ROLES ===")
            for r in roles:
                tools = ", ".join(r.get("tool_allowlist") or []) or "unrestricted"
                print("  [%s] %s  tools: %s" % (r["name"], r["description"], tools))
    finally:
        conn.close()


def cmd_role_get(args):
    """Get full details of an agent role including system prompt."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM clambake.agent_roles WHERE name = %s", (args.name,))
            r = cur.fetchone()
            if not r:
                print("ERROR: Role '%s' not found" % args.name)
                sys.exit(1)
            print("ROLE: %s" % r["name"])
            print("  Description: %s" % r["description"])
            print("  Capabilities: %s" % ", ".join(r["capabilities"] or []))
            allowlist = r.get("tool_allowlist") or []
            denylist = r.get("tool_denylist") or []
            print("  Tool Allowlist: %s" % (", ".join(allowlist) if allowlist else "unrestricted"))
            if denylist:
                print("  Tool Denylist: %s" % ", ".join(denylist))
            print("  System Prompt:\n%s" % r["system_prompt"])
    finally:
        conn.close()


def cmd_role_create(args):
    """Create or update an agent role."""
    caps = [c.strip() for c in args.capabilities.split(",")] if args.capabilities else []
    allowlist = [t.strip() for t in args.tool_allowlist.split(",")] if args.tool_allowlist else []
    denylist = [t.strip() for t in args.tool_denylist.split(",")] if args.tool_denylist else []
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.agent_roles
                    (name, description, system_prompt, capabilities, tool_allowlist, tool_denylist)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    description = EXCLUDED.description,
                    system_prompt = EXCLUDED.system_prompt,
                    capabilities = EXCLUDED.capabilities,
                    tool_allowlist = EXCLUDED.tool_allowlist,
                    tool_denylist = EXCLUDED.tool_denylist,
                    updated_at = NOW()
            """, (args.name, args.description, args.prompt, caps, allowlist, denylist))
        conn.commit()
        print("ROLE: '%s' saved" % args.name)
    finally:
        conn.close()


def cmd_role_get_tools(args):
    """Output tool allowlist for shell consumption (used by agent-worker.sh)."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT tool_allowlist, tool_denylist
                FROM clambake.agent_roles WHERE name = %s
            """, (args.name,))
            r = cur.fetchone()
            if not r:
                print("ERROR: Role '%s' not found" % args.name)
                sys.exit(1)
            allowlist = r.get("tool_allowlist") or []
            if not allowlist:
                print("none")
                return
            if args.format == "allowlist":
                # Space-separated for shell --allowedTools flag
                print(" ".join(allowlist))
            elif args.format == "json":
                print(json.dumps(allowlist))
            else:
                for t in allowlist:
                    print(t)
    finally:
        conn.close()


def cmd_role_seed(args):
    """Seed the eight default agent roles with tool-gating."""
    roles = [
        {
            "name": "scout",
            "description": "Read-only recon: explores codebase, reports structure/patterns/issues.",
            "system_prompt": (
                "You are the Scout. Your job is to explore the codebase and report what you find.\n\n"
                "RULES:\n"
                "- Read files, search for patterns, understand project structure\n"
                "- Report findings: file organization, dependencies, potential issues, patterns\n"
                "- Store important discoveries via 'clambake remember'\n"
                "- Send findings to other agents via 'clambake send'\n"
                "- You CANNOT edit files or run non-git commands\n"
                "- Focus on thoroughness — other agents depend on your recon"
            ),
            "capabilities": ["read_code", "search", "report"],
            "tool_allowlist": ["Read", "Glob", "Grep", "Bash(git:*)"],
        },
        {
            "name": "planner",
            "description": "Designs architecture, writes specs, dispatches subtasks. Does not code.",
            "system_prompt": (
                "You are the Planner. Your job is to read the codebase, understand the architecture, "
                "and write detailed implementation specs for other agents.\n\n"
                "RULES:\n"
                "- Read and analyze code extensively before writing a spec\n"
                "- Break large tasks into discrete, non-overlapping subtasks\n"
                "- Each subtask should specify: files to create/modify, expected behavior, acceptance criteria\n"
                "- Assign a file_scope to each subtask so agents don't conflict\n"
                "- DO NOT write code — only specs and plans\n"
                "- Use 'clambake task-create' to dispatch subtasks when your plan is ready\n"
                "- Use 'clambake remember' to store architecture decisions"
            ),
            "capabilities": ["read_code", "write_specs", "create_tasks"],
            "tool_allowlist": ["Read", "Glob", "Grep", "Bash(git:*)"],
        },
        {
            "name": "plan-reviewer",
            "description": "Critiques plans for completeness, feasibility, and risks.",
            "system_prompt": (
                "You are the Plan Reviewer. You critique implementation plans from the Planner.\n\n"
                "RULES:\n"
                "- Read the plan/spec in your task description carefully\n"
                "- Read the relevant codebase to verify feasibility\n"
                "- Provide structured feedback: strengths, issues, missing items, recommendations\n"
                "- Flag risks: breaking changes, performance concerns, security gaps\n"
                "- You CANNOT edit files — only read and provide feedback\n"
                "- Use 'clambake send' to communicate feedback to the planner\n"
                "- When done, mark task done with your review as the result"
            ),
            "capabilities": ["read_code", "review_plans"],
            "tool_allowlist": ["Read", "Glob", "Grep", "Bash(git:*)"],
        },
        {
            "name": "coder",
            "description": "Implements features and fixes bugs according to specs. Does not test.",
            "system_prompt": (
                "You are the Coder. You implement code according to the spec in your task description.\n\n"
                "RULES:\n"
                "- Read the task description carefully — it is your spec\n"
                "- Only modify files listed in your task's file_scope\n"
                "- Write clean, working code that meets the acceptance criteria\n"
                "- DO NOT write tests — QA handles that\n"
                "- DO NOT refactor code outside your scope\n"
                "- When done, run 'clambake task-done <id>' with a summary of what you built\n"
                "- If blocked, run 'clambake task-fail <id> --result \"reason\"' and it will be reassigned"
            ),
            "capabilities": ["write_code", "read_code"],
            "tool_allowlist": ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
        },
        {
            "name": "qa",
            "description": "Writes tests, runs them, finds bugs. Reports issues but does not fix them.",
            "system_prompt": (
                "You are QA. You test code that other agents have written.\n\n"
                "RULES:\n"
                "- Read the original task spec to understand expected behavior\n"
                "- Write tests that verify the acceptance criteria\n"
                "- Run the tests and report results\n"
                "- If you find bugs, use 'clambake task-create' to file a bug fix task for the coder\n"
                "- DO NOT fix bugs yourself — report them\n"
                "- When all tests pass, run 'clambake task-done <id>' with test results\n"
                "- Use 'clambake send' to notify the coder of any issues found"
            ),
            "capabilities": ["read_code", "write_tests", "run_tests", "create_tasks"],
            "tool_allowlist": ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
        },
        {
            "name": "reviewer",
            "description": "Reviews code for quality, security, and patterns. Can run tests but cannot edit.",
            "system_prompt": (
                "You are the Reviewer. You review code changes for quality and correctness.\n\n"
                "RULES:\n"
                "- Read the task spec and the code that was written\n"
                "- Check for: correctness, security issues, code quality, adherence to patterns\n"
                "- You can run tests and commands to verify behavior\n"
                "- If approved, run 'clambake task-done <id>' with your review notes\n"
                "- If rejected, run 'clambake task-fail <id>' with specific feedback\n"
                "- DO NOT modify code yourself — only review and provide feedback\n"
                "- Use 'clambake remember' to document patterns you want enforced"
            ),
            "capabilities": ["read_code", "run_tests", "review"],
            "tool_allowlist": ["Read", "Glob", "Grep", "Bash"],
        },
        {
            "name": "documenter",
            "description": "Writes and updates documentation. No shell access.",
            "system_prompt": (
                "You are the Documenter. You write and maintain project documentation.\n\n"
                "RULES:\n"
                "- Read existing code and docs to understand the project\n"
                "- Write clear, accurate documentation (README, API docs, inline comments)\n"
                "- Update existing docs when code changes\n"
                "- You CANNOT run shell commands — documentation only\n"
                "- Use 'clambake remember' to store documentation decisions\n"
                "- When done, mark task done with a list of files updated"
            ),
            "capabilities": ["read_code", "write_docs"],
            "tool_allowlist": ["Read", "Glob", "Grep", "Edit", "Write"],
        },
        {
            "name": "red-team",
            "description": "Security testing: finds vulnerabilities, files fix tasks for critical findings.",
            "system_prompt": (
                "You are the Red Team agent. You find security vulnerabilities.\n\n"
                "RULES:\n"
                "- Read code looking for security issues (injection, auth bypass, data exposure, etc.)\n"
                "- Run commands to test for vulnerabilities (curl, network probes, etc.)\n"
                "- You CANNOT edit code — only identify and report issues\n"
                "- For critical findings, use 'clambake task-create' to file a fix task for the coder\n"
                "- Classify findings: critical, high, medium, low\n"
                "- Use 'clambake send' to alert team of critical findings\n"
                "- When done, mark task done with a security report as the result"
            ),
            "capabilities": ["read_code", "security_testing", "create_tasks"],
            "tool_allowlist": ["Read", "Glob", "Grep", "Bash"],
        },
    ]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for r in roles:
                cur.execute("""
                    INSERT INTO clambake.agent_roles
                        (name, description, system_prompt, capabilities, tool_allowlist)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        description = EXCLUDED.description,
                        system_prompt = EXCLUDED.system_prompt,
                        capabilities = EXCLUDED.capabilities,
                        tool_allowlist = EXCLUDED.tool_allowlist,
                        updated_at = NOW()
                """, (r["name"], r["description"], r["system_prompt"],
                      r["capabilities"], r["tool_allowlist"]))
        conn.commit()
        role_names = ", ".join(r["name"] for r in roles)
        print("SEEDED: %d agent roles (%s)" % (len(roles), role_names))
    finally:
        conn.close()


def cmd_pipeline_seed(args):
    """Seed the default pipeline templates."""
    templates = [
        {
            "name": "plan-build-review",
            "description": "Standard dev cycle: plan, build, review",
            "steps": json.dumps([
                {"step": 1, "role": "planner",
                 "title_template": "Plan: $INPUT",
                 "description_template": "Analyze the codebase and create a detailed implementation plan for: $INPUT"},
                {"step": 2, "role": "coder",
                 "title_template": "Build: $INPUT",
                 "description_template": "Implement the following plan:\n\n$PREV_RESULT"},
                {"step": 3, "role": "reviewer",
                 "title_template": "Review: $INPUT",
                 "description_template": "Review the implementation:\n\nOriginal request: $ORIGINAL\n\nImplementation notes:\n$PREV_RESULT"},
            ]),
        },
        {
            "name": "full-pipeline",
            "description": "Complete cycle: scout, plan, build, test, review",
            "steps": json.dumps([
                {"step": 1, "role": "scout",
                 "title_template": "Scout: $INPUT",
                 "description_template": "Explore the codebase and report relevant findings for: $INPUT"},
                {"step": 2, "role": "planner",
                 "title_template": "Plan: $INPUT",
                 "description_template": "Create an implementation plan based on scout findings:\n\n$PREV_RESULT\n\nOriginal request: $ORIGINAL"},
                {"step": 3, "role": "coder",
                 "title_template": "Build: $INPUT",
                 "description_template": "Implement the following plan:\n\n$PREV_RESULT"},
                {"step": 4, "role": "qa",
                 "title_template": "Test: $INPUT",
                 "description_template": "Write and run tests for the implementation:\n\nOriginal request: $ORIGINAL\n\nImplementation notes:\n$PREV_RESULT"},
                {"step": 5, "role": "reviewer",
                 "title_template": "Review: $INPUT",
                 "description_template": "Final review of the implementation:\n\nOriginal request: $ORIGINAL\n\nTest results:\n$PREV_RESULT"},
            ]),
        },
        {
            "name": "plan-review-plan",
            "description": "Iterative planning: plan, critique, refine",
            "steps": json.dumps([
                {"step": 1, "role": "planner",
                 "title_template": "Draft plan: $INPUT",
                 "description_template": "Create an initial implementation plan for: $INPUT"},
                {"step": 2, "role": "plan-reviewer",
                 "title_template": "Critique plan: $INPUT",
                 "description_template": "Review this plan for completeness, feasibility, and risks:\n\n$PREV_RESULT"},
                {"step": 3, "role": "planner",
                 "title_template": "Refine plan: $INPUT",
                 "description_template": "Revise the plan based on reviewer feedback:\n\nOriginal plan: $ORIGINAL\n\nReview feedback:\n$PREV_RESULT"},
            ]),
        },
        {
            "name": "build-test",
            "description": "Quick build and test cycle",
            "steps": json.dumps([
                {"step": 1, "role": "coder",
                 "title_template": "Build: $INPUT",
                 "description_template": "Implement: $INPUT"},
                {"step": 2, "role": "qa",
                 "title_template": "Test: $INPUT",
                 "description_template": "Write and run tests for the implementation:\n\nOriginal request: $ORIGINAL\n\nImplementation notes:\n$PREV_RESULT"},
            ]),
        },
        {
            "name": "security-audit",
            "description": "Security focused: scout, red-team, document",
            "steps": json.dumps([
                {"step": 1, "role": "scout",
                 "title_template": "Recon for security: $INPUT",
                 "description_template": "Explore the codebase with a security focus. Map attack surfaces, auth flows, data handling for: $INPUT"},
                {"step": 2, "role": "red-team",
                 "title_template": "Security test: $INPUT",
                 "description_template": "Perform security testing based on recon findings:\n\n$PREV_RESULT\n\nOriginal scope: $ORIGINAL"},
                {"step": 3, "role": "documenter",
                 "title_template": "Security report: $INPUT",
                 "description_template": "Write a security audit report based on findings:\n\nOriginal scope: $ORIGINAL\n\nSecurity findings:\n$PREV_RESULT"},
            ]),
        },
    ]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for t in templates:
                cur.execute("""
                    INSERT INTO clambake.pipeline_templates (name, description, steps)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (name) DO UPDATE SET
                        description = EXCLUDED.description,
                        steps = EXCLUDED.steps,
                        updated_at = NOW()
                """, (t["name"], t["description"], t["steps"]))
        conn.commit()
        names = ", ".join(t["name"] for t in templates)
        print("SEEDED: %d pipeline templates (%s)" % (len(templates), names))
    finally:
        conn.close()


def cmd_pipeline_list(args):
    """List all pipeline templates."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT name, description, steps
                FROM clambake.pipeline_templates ORDER BY name
            """)
            templates = cur.fetchall()
            if not templates:
                print("PIPELINES: none defined. Run 'clambake pipeline-seed' to create defaults.")
                return
            print("=== PIPELINE TEMPLATES ===")
            for t in templates:
                steps = t["steps"] if isinstance(t["steps"], list) else json.loads(t["steps"])
                roles = " -> ".join(s["role"] for s in steps)
                print("  [%s] %s  (%d steps: %s)" % (
                    t["name"], t["description"], len(steps), roles))
    finally:
        conn.close()


def cmd_pipeline_run(args):
    """Create chained tasks from a pipeline template."""
    instance_id, _ = get_instance_id()
    created_by = instance_id or "human"

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Fetch template
            cur.execute("""
                SELECT * FROM clambake.pipeline_templates WHERE name = %s
            """, (args.pipeline,))
            template = cur.fetchone()
            if not template:
                print("ERROR: Pipeline '%s' not found. Run 'clambake pipeline-list'." % args.pipeline)
                sys.exit(1)

            steps = template["steps"] if isinstance(template["steps"], list) else json.loads(template["steps"])
            run_id = "pipe-%s" % str(uuid.uuid4())[:8]
            task_ids = []

            for step_def in steps:
                step_num = step_def["step"]
                # Substitute $INPUT and $ORIGINAL (both are the user's input)
                title = step_def["title_template"].replace("$INPUT", args.input).replace("$ORIGINAL", args.input)
                desc = step_def["description_template"].replace("$INPUT", args.input).replace("$ORIGINAL", args.input)
                # $PREV_RESULT stays as a placeholder — resolved at claim time by agent-worker.sh

                depends = [task_ids[-1]] if task_ids else []

                cur.execute("""
                    INSERT INTO clambake.tasks
                        (title, description, project, priority, assigned_role,
                         depends_on, created_by, pipeline_run_id, pipeline_step)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (title, desc, args.project, args.priority, step_def["role"],
                      depends, created_by, run_id, step_num))
                task_id = cur.fetchone()["id"]
                task_ids.append(task_id)

        conn.commit()

        print("PIPELINE RUN: %s" % run_id)
        print("  Template: %s (%s)" % (template["name"], template["description"]))
        print("  Project: %s" % args.project)
        print("  Tasks created: %d" % len(task_ids))
        for i, (step_def, tid) in enumerate(zip(steps, task_ids)):
            deps_str = " (depends on #%d)" % task_ids[i - 1] if i > 0 else " (ready)"
            print("    Step %d: #%d [%s] %s%s" % (
                step_def["step"], tid, step_def["role"],
                step_def["title_template"].replace("$INPUT", args.input)[:60], deps_str))
    finally:
        conn.close()


def cmd_pipeline_status(args):
    """Show pipeline run progress."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if args.run_id:
                # Specific run
                cur.execute("""
                    SELECT * FROM clambake.pipeline_runs WHERE pipeline_run_id = %s
                """, (args.run_id,))
                run = cur.fetchone()
                if not run:
                    print("ERROR: Pipeline run '%s' not found" % args.run_id)
                    sys.exit(1)

                print("PIPELINE: %s [%s]" % (run["pipeline_run_id"], run["pipeline_status"]))
                print("  Project: %s" % run["project"])
                print("  Progress: %d/%d completed, %d active, %d failed" % (
                    run["completed_steps"], run["total_steps"],
                    run["active_steps"], run["failed_steps"]))

                # Show individual tasks
                cur.execute("""
                    SELECT id, pipeline_step, title, status, assigned_role, assigned_instance, result
                    FROM clambake.tasks
                    WHERE pipeline_run_id = %s
                    ORDER BY pipeline_step
                """, (args.run_id,))
                tasks = cur.fetchall()
                print("\n  Steps:")
                for t in tasks:
                    inst = t["assigned_instance"][:8] if t["assigned_instance"] else "-"
                    result_preview = ""
                    if t["result"]:
                        result_preview = " — %s" % t["result"][:80]
                    print("    Step %d: #%d [%s] %s -> %s%s" % (
                        t["pipeline_step"], t["id"], t["status"],
                        t["assigned_role"], inst, result_preview))
            else:
                # List all recent runs
                cur.execute("""
                    SELECT * FROM clambake.pipeline_runs
                    ORDER BY started_at DESC LIMIT 20
                """)
                runs = cur.fetchall()
                if not runs:
                    print("PIPELINE RUNS: none")
                    return
                print("=== PIPELINE RUNS ===")
                for r in runs:
                    ts = r["started_at"].strftime("%m/%d %H:%M") if r["started_at"] else "?"
                    print("  [%s] %s (%s) %d/%d steps — %s" % (
                        r["pipeline_status"], r["pipeline_run_id"], r["project"],
                        r["completed_steps"], r["total_steps"], ts))
    finally:
        conn.close()


def cmd_pipeline_prev_result(args):
    """Get the previous pipeline step's result (internal, for agent-worker.sh)."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get this task's pipeline info
            cur.execute("""
                SELECT pipeline_run_id, pipeline_step
                FROM clambake.tasks WHERE id = %s
            """, (args.task_id,))
            task = cur.fetchone()
            if not task or not task["pipeline_run_id"]:
                print("")
                return

            if task["pipeline_step"] <= 1:
                print("")
                return

            # Get previous step's result
            prev_step = task["pipeline_step"] - 1
            cur.execute("""
                SELECT result FROM clambake.tasks
                WHERE pipeline_run_id = %s AND pipeline_step = %s
            """, (task["pipeline_run_id"], prev_step))
            prev = cur.fetchone()
            if prev and prev["result"]:
                print(prev["result"])
            else:
                print("")
    finally:
        conn.close()


def cmd_task_get_meta(args):
    """Get a single task field (internal, for agent-worker.sh)."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM clambake.tasks WHERE id = %s", (args.task_id,))
            task = cur.fetchone()
            if not task:
                print("")
                return
            val = task.get(args.field, "")
            if val is None:
                print("")
            elif isinstance(val, list):
                print(",".join(str(x) for x in val))
            else:
                print(str(val))
    finally:
        conn.close()


def cmd_task_create(args):
    """Create a new task."""
    instance_id, _ = get_instance_id()
    created_by = instance_id or "human"
    depends = [int(x.strip()) for x in args.depends_on.split(",")] if args.depends_on else []
    files = [f.strip() for f in args.file_scope.split(",")] if args.file_scope else []

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.tasks
                    (title, description, project, priority, assigned_role,
                     file_scope, depends_on, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (args.title, args.description, args.project, args.priority,
                  args.role, files, depends, created_by))
            task_id = cur.fetchone()[0]
        conn.commit()
        role_str = " [%s]" % args.role if args.role else ""
        print("TASK #%d: %s%s — %s" % (task_id, args.project, role_str, args.title))
    finally:
        conn.close()


def cmd_task_list(args):
    """List tasks, optionally filtered."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = "SELECT * FROM clambake.tasks WHERE TRUE"
            params = []
            if args.project:
                query += " AND project = %s"
                params.append(args.project)
            if args.status:
                query += " AND status = %s"
                params.append(args.status)
            if args.role:
                query += " AND assigned_role = %s"
                params.append(args.role)
            if args.available:
                query = "SELECT * FROM clambake.available_tasks WHERE TRUE"
                params = []
                if args.project:
                    query += " AND project = %s"
                    params.append(args.project)
                if args.role:
                    query += " AND assigned_role = %s"
                    params.append(args.role)
            else:
                query += " ORDER BY priority DESC, created_at ASC"
            cur.execute(query, params)
            tasks = cur.fetchall()

            if not tasks:
                print("TASKS: none found")
                return

            print("=== TASKS (%d) ===" % len(tasks))
            for t in tasks:
                role = t["assigned_role"] or "any"
                inst = t["assigned_instance"][:8] if t["assigned_instance"] else "-"
                deps = ",".join(str(d) for d in (t["depends_on"] or []))
                deps_str = " deps:[%s]" % deps if deps else ""
                print("  #%d [%s] %s (%s) -> %s%s — %s" % (
                    t["id"], t["status"], t["project"], role, inst,
                    deps_str, t["title"]))
    finally:
        conn.close()


def cmd_task_claim(args):
    """Claim a pending task for the current instance."""
    instance_id, project = get_instance_id()
    if not instance_id:
        print("ERROR: Not registered.")
        sys.exit(1)

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Atomically claim: only if still pending
            cur.execute("""
                UPDATE clambake.tasks
                SET status = 'claimed',
                    assigned_instance = %s,
                    claimed_at = NOW()
                WHERE id = %s AND status = 'pending'
                RETURNING id, title, assigned_role, description, file_scope
            """, (instance_id, args.task_id))
            task = cur.fetchone()
            if not task:
                print("ERROR: Task #%s not available (already claimed or doesn't exist)" % args.task_id)
                sys.exit(1)
        conn.commit()

        print("CLAIMED: #%d — %s" % (task["id"], task["title"]))
        if task["assigned_role"]:
            # Fetch the role's system prompt
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT system_prompt FROM clambake.agent_roles WHERE name = %s",
                            (task["assigned_role"],))
                role = cur.fetchone()
                if role:
                    print("\n=== ROLE: %s ===" % task["assigned_role"])
                    print(role["system_prompt"])
        if task["description"]:
            print("\n=== SPEC ===")
            print(task["description"])
        if task["file_scope"]:
            print("\n=== FILE SCOPE ===")
            for f in task["file_scope"]:
                print("  %s" % f)

        # Update instance current_task
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE clambake.instances
                SET current_task = %s, status = 'busy', last_heartbeat = NOW()
                WHERE instance_id = %s
            """, (task["title"], instance_id))
        conn.commit()
    finally:
        conn.close()


def cmd_task_done(args):
    """Mark a task as completed."""
    instance_id, _ = get_instance_id()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE clambake.tasks
                SET status = 'done', result = %s, completed_at = NOW()
                WHERE id = %s AND assigned_instance = %s
                RETURNING id
            """, (args.result, args.task_id, instance_id))
            if cur.rowcount == 0:
                # Allow without instance check (for human/admin)
                cur.execute("""
                    UPDATE clambake.tasks
                    SET status = 'done', result = %s, completed_at = NOW()
                    WHERE id = %s
                    RETURNING id
                """, (args.result, args.task_id))
                if cur.rowcount == 0:
                    print("ERROR: Task #%s not found" % args.task_id)
                    sys.exit(1)
        conn.commit()

        # Clear instance current_task
        if instance_id:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE clambake.instances
                    SET current_task = NULL, status = 'active', last_heartbeat = NOW()
                    WHERE instance_id = %s
                """, (instance_id,))
            conn.commit()
        print("DONE: Task #%s completed" % args.task_id)
    finally:
        conn.close()


def cmd_task_fail(args):
    """Mark a task as failed."""
    instance_id, _ = get_instance_id()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE clambake.tasks
                SET status = 'failed', result = %s, completed_at = NOW()
                WHERE id = %s
                RETURNING id
            """, (args.result, args.task_id))
            if cur.rowcount == 0:
                print("ERROR: Task #%s not found" % args.task_id)
                sys.exit(1)
        conn.commit()

        if instance_id:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE clambake.instances
                    SET current_task = NULL, status = 'active', last_heartbeat = NOW()
                    WHERE instance_id = %s
                """, (instance_id,))
            conn.commit()
        print("FAILED: Task #%s — %s" % (args.task_id, args.result or "no reason given"))
    finally:
        conn.close()


def cmd_update_memory(args):
    """Update an existing memory entry."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            updates = ["updated_at = NOW()"]
            params = []
            if args.content:
                updates.append("content = %s")
                params.append(args.content)
            if args.status:
                if args.glob:
                    print("WARNING: --status is not supported for global memory (no status column). Ignoring.")
                else:
                    updates.append("status = %s")
                    params.append(args.status)
            if args.title:
                updates.append("title = %s")
                params.append(args.title)

            # Re-generate embedding if title or content changed
            if args.title or args.content:
                table = "clambake.global_memory" if args.glob else "clambake.project_memory"
                cur.execute("SELECT title, content FROM %s WHERE id = %%s" % table,
                            (args.memory_id,))
                existing = cur.fetchone()
                if existing:
                    new_title = args.title or existing["title"]
                    new_content = args.content or existing["content"]
                    embedding = generate_embedding(new_title + "\n" + new_content)
                    if embedding:
                        updates.append("embedding = %s")
                        params.append(embedding)

            params.append(args.memory_id)

            table = "clambake.global_memory" if args.glob else "clambake.project_memory"
            cur.execute(
                "UPDATE %s SET %s WHERE id = %%s" % (table, ", ".join(updates)),
                params
            )
            if cur.rowcount == 0:
                print("ERROR: Memory #%s not found" % args.memory_id)
                sys.exit(1)
        conn.commit()
        print("UPDATED: memory #%s" % args.memory_id)
    finally:
        conn.close()


def cmd_digest(args):
    """Show activity summary for last N hours."""
    hours = args.hours
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            print("=== CLAMBAKE DIGEST (last %dh) ===" % hours)

            # Active instances
            cur.execute("SELECT * FROM clambake.active_instances")
            instances = cur.fetchall()
            print("\n--- Active Instances ---")
            if not instances:
                print("  (none)")
            for i in instances:
                task = i["current_task"] or "idle"
                age = i["seconds_since_heartbeat"]
                stale = " [!] STALE" if age > 300 else ""
                print("  [%s] %s — %s (heartbeat %ds ago)%s" % (
                    i["status"], i["project"], task, age, stale))

            # Stale instances (heartbeat > 5 min but < 2h — not yet cleaned)
            cur.execute("""
                SELECT instance_id, project, current_task,
                       EXTRACT(EPOCH FROM (NOW() - last_heartbeat))::int AS age
                FROM clambake.instances
                WHERE last_heartbeat < NOW() - INTERVAL '5 minutes'
                  AND last_heartbeat > NOW() - INTERVAL '2 hours'
            """)
            stale = cur.fetchall()
            if stale:
                print("\n--- [!] Stale Instances ---")
                for s in stale:
                    print("  %s (%s) — no heartbeat for %ds" % (
                        s["project"], s["instance_id"], s["age"]))

            # Active blockers
            cur.execute("""
                SELECT id, from_project, subject, created_at
                FROM clambake.messages
                WHERE message_type = 'blocker' AND NOT is_read
                  AND created_at > NOW() - INTERVAL '%s hours'
            """, (hours,))
            blockers = cur.fetchall()
            if blockers:
                print("\n--- [!!] Active Blockers ---")
                for b in blockers:
                    ts = b["created_at"].strftime("%m/%d %H:%M")
                    print("  #%d [%s] %s — %s" % (
                        b["id"], ts, b["from_project"] or "?", b["subject"]))

            # Task counts
            cur.execute("""
                SELECT status, COUNT(*) as cnt
                FROM clambake.tasks
                WHERE created_at > NOW() - INTERVAL '%s hours'
                   OR status IN ('pending', 'claimed', 'in_progress')
                GROUP BY status
                ORDER BY status
            """, (hours,))
            task_counts = {r["status"]: r["cnt"] for r in cur.fetchall()}
            print("\n--- Tasks ---")
            for status in ["pending", "claimed", "in_progress", "done", "failed"]:
                cnt = task_counts.get(status, 0)
                if cnt:
                    print("  %s: %d" % (status, cnt))
            if not task_counts:
                print("  (no tasks)")

            # Recently completed tasks
            cur.execute("""
                SELECT id, title, project, assigned_role, completed_at
                FROM clambake.tasks
                WHERE status = 'done'
                  AND completed_at > NOW() - INTERVAL '%s hours'
                ORDER BY completed_at DESC LIMIT 10
            """, (hours,))
            done_tasks = cur.fetchall()
            if done_tasks:
                print("\n--- Recently Completed ---")
                for t in done_tasks:
                    ts = t["completed_at"].strftime("%m/%d %H:%M")
                    role = t["assigned_role"] or "any"
                    print("  #%d [%s] %s (%s) — %s" % (
                        t["id"], ts, t["project"], role, t["title"]))

            # Memory additions
            cur.execute("""
                SELECT COUNT(*) as cnt FROM clambake.project_memory
                WHERE created_at > NOW() - INTERVAL '%s hours'
            """, (hours,))
            proj_mem = cur.fetchone()["cnt"]
            cur.execute("""
                SELECT COUNT(*) as cnt FROM clambake.global_memory
                WHERE created_at > NOW() - INTERVAL '%s hours'
            """, (hours,))
            glob_mem = cur.fetchone()["cnt"]
            if proj_mem or glob_mem:
                print("\n--- Memory ---")
                print("  New project memories: %d" % proj_mem)
                print("  New global memories: %d" % glob_mem)

            # Messages sent
            cur.execute("""
                SELECT message_type, COUNT(*) as cnt
                FROM clambake.messages
                WHERE created_at > NOW() - INTERVAL '%s hours'
                GROUP BY message_type
            """, (hours,))
            msg_counts = cur.fetchall()
            if msg_counts:
                print("\n--- Messages ---")
                for m in msg_counts:
                    print("  %s: %d" % (m["message_type"], m["cnt"]))

            # Session log summary
            cur.execute("""
                SELECT action, COUNT(*) as cnt
                FROM clambake.session_log
                WHERE created_at > NOW() - INTERVAL '%s hours'
                GROUP BY action
                ORDER BY cnt DESC
            """, (hours,))
            log_counts = cur.fetchall()
            if log_counts:
                print("\n--- Session Log ---")
                for l in log_counts:
                    print("  %s: %d" % (l["action"], l["cnt"]))
    finally:
        conn.close()


def cmd_embed_backfill(args):
    """Generate embeddings for all memories that don't have them."""
    conn = get_conn()
    try:
        success = 0
        fail = 0
        total = 0

        for table in ["clambake.project_memory", "clambake.global_memory"]:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT id, title, content FROM %s WHERE embedding IS NULL" % table)
                rows = cur.fetchall()
                total += len(rows)

                for r in rows:
                    text = r["title"] + "\n" + r["content"]
                    embedding = generate_embedding(text)
                    if embedding:
                        cur.execute(
                            "UPDATE %s SET embedding = %%s WHERE id = %%s" % table,
                            (embedding, r["id"])
                        )
                        success += 1
                        print("  OK: %s #%d — %s" % (table.split(".")[-1], r["id"], r["title"][:60]))
                    else:
                        fail += 1
                        print("  FAIL: %s #%d — %s" % (table.split(".")[-1], r["id"], r["title"][:60]))
            conn.commit()

        print("\nBACKFILL: %d total, %d embedded, %d failed" % (total, success, fail))
    finally:
        conn.close()


# --- New unified commands: up / down / infra / project-list ------------------

def cmd_up(args):
    """One-command session startup: register + inbox + recall project + recall global warnings + check infra."""
    conn = get_conn_safe()
    if not conn:
        print("CLAMBAKE: Postgres unreachable (localhost:%s). Running without coordination." % DB_PORT)
        return

    try:
        working_dir = args.dir or os.getcwd()
        project = args.project or detect_project(working_dir)
        instance_id = str(uuid.uuid4())[:12]
        model = args.model or "opus"
        role = getattr(args, "role", None) or os.environ.get("CLAMBAKE_ROLE")

        # Auto-cleanup stale data
        counts = _run_cleanup(conn)
        cleaned = sum(v for v in counts.values() if isinstance(v, int))

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Register
            cur.execute("""
                INSERT INTO clambake.instances
                    (instance_id, project, working_dir, model, role, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
                ON CONFLICT (instance_id) DO UPDATE SET
                    last_heartbeat = NOW(), status = 'active', role = EXCLUDED.role
            """, (instance_id, project, working_dir, model, role))
            # Log session start to session_log
            cur.execute("""
                INSERT INTO clambake.session_log
                    (instance_id, project, action, summary)
                VALUES (%s, %s, 'started', 'Session started')
            """, (instance_id, project))
        conn.commit()
        save_instance_id(instance_id, project, role)

        role_tag = " <%s>" % role if role else ""
        print("=== CLAMBAKE UP ===")
        print("  Instance: %s%s | Project: %s" % (instance_id, role_tag, project))
        if cleaned > 0:
            print("  Auto-cleaned %d stale entries" % cleaned)

        # Other active instances
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT instance_id, project, current_task, role, status
                FROM clambake.active_instances
                WHERE instance_id != %s
            """, (instance_id,))
            others = cur.fetchall()
            if others:
                print("\n--- Active Instances ---")
                for o in others:
                    task = o["current_task"] or "idle"
                    role_tag = " <%s>" % o["role"] if o.get("role") else ""
                    print("  [%s]%s %s — %s (%s)" % (
                        o["status"], role_tag, o["project"], task, o["instance_id"]))

            # Unread messages
            cur.execute("""
                SELECT id, from_project, message_type, subject, body, created_at
                FROM clambake.messages
                WHERE (to_target IN (%s, %s, '@all')) AND NOT is_read
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY created_at DESC LIMIT 10
            """, (instance_id, project))
            messages = cur.fetchall()
            if messages:
                print("\n--- Inbox (%d unread) ---" % len(messages))
                for m in messages:
                    proj = m["from_project"] or "?"
                    print("  [%s] %s — %s" % (m["message_type"], proj, m["subject"]))

            # Infrastructure warnings
            cur.execute("""
                SELECT service, status, message FROM clambake.current_infra
                WHERE status IN ('down', 'degraded', 'warning')
            """)
            warnings = cur.fetchall()
            if warnings:
                print("\n--- [!] Infrastructure Warnings ---")
                for w in warnings:
                    print("  [%s] %s — %s" % (w["status"].upper(), w["service"], w["message"] or ""))

            # Project memories (semantic if possible, else recent)
            project_embedding = generate_embedding(project, prefix="search_query: ")
            if project_embedding:
                cur.execute("""
                    SELECT id, memory_type, title, content,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM clambake.project_memory
                    WHERE project = %s AND status = 'active'
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT 5
                """, (project_embedding, project, project_embedding))
            else:
                cur.execute("""
                    SELECT id, memory_type, title, content, NULL::float AS similarity
                    FROM clambake.project_memory
                    WHERE project = %s AND status = 'active'
                    ORDER BY updated_at DESC
                    LIMIT 5
                """, (project,))
            memories = cur.fetchall()
            if memories:
                print("\n--- Project Memories (%s) ---" % project)
                for m in memories:
                    sim = m.get("similarity")
                    sim_str = " [%.2f]" % sim if sim is not None else ""
                    preview = m["content"][:200]
                    if len(m["content"]) > 200:
                        preview += "..."
                    print("  #%d [%s]%s %s" % (m["id"], m["memory_type"], sim_str, m["title"]))

            # Global warnings/lessons (recent)
            cur.execute("""
                SELECT id, memory_type, title FROM clambake.global_memory
                WHERE memory_type IN ('infrastructure', 'lesson')
                ORDER BY updated_at DESC LIMIT 5
            """)
            global_mem = cur.fetchall()
            if global_mem:
                print("\n--- Global Knowledge ---")
                for g in global_mem:
                    print("  #%d [%s] %s" % (g["id"], g["memory_type"], g["title"]))

        print("\n=== READY ===")
    finally:
        conn.close()


def cmd_down(args):
    """One-command session shutdown: deregister + optional summary."""
    instance_id, project = get_instance_id()
    if not instance_id:
        print("Not registered.")
        return

    conn = get_conn_safe()
    if not conn:
        clear_instance_id()
        print("DEREGISTERED: %s (Postgres unreachable, cleared local state)" % instance_id)
        return

    try:
        with conn.cursor() as cur:
            # Log shutdown with optional summary
            summary = args.summary if hasattr(args, 'summary') and args.summary else "Session ended"
            cur.execute("""
                INSERT INTO clambake.session_log
                    (instance_id, project, action, summary)
                VALUES (%s, %s, 'shutdown', %s)
            """, (instance_id, project, summary))
            cur.execute(
                "DELETE FROM clambake.instances WHERE instance_id = %s",
                (instance_id,)
            )
        conn.commit()
        clear_instance_id()
        print("CLAMBAKE DOWN: %s (%s)" % (instance_id, project))
    finally:
        conn.close()


def cmd_infra(args):
    """Show current infrastructure status."""
    conn = get_conn_safe()
    if not conn:
        print("CLAMBAKE: Postgres unreachable. Cannot check infrastructure.")
        return

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM clambake.current_infra")
            rows = cur.fetchall()

            print("=== INFRASTRUCTURE STATUS ===")
            if not rows:
                print("  (no services reported — use 'clambake infra-warn' to report)")
                return
            for r in rows:
                status = r["status"].upper()
                marker = "  " if r["status"] == "up" else "[!]"
                port_str = ":%d" % r["port"] if r["port"] else ""
                container_str = " (%s)" % r["container"] if r["container"] else ""
                ttl = r["seconds_until_expiry"]
                ttl_str = " [expires %dm]" % (ttl // 60) if ttl < 3600 else ""
                msg = " — %s" % r["message"] if r["message"] else ""
                print(" %s [%s] %s%s%s%s%s" % (
                    marker, status, r["service"], port_str, container_str, ttl_str, msg))
    finally:
        conn.close()


def cmd_infra_warn(args):
    """Report infrastructure status (upserts by service name)."""
    instance_id, _ = get_instance_id()
    reported_by = instance_id or "human"

    conn = get_conn_safe()
    if not conn:
        print("CLAMBAKE: Postgres unreachable.")
        return

    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.infra_state
                    (service, status, port, container, message, reported_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (service) DO UPDATE SET
                    status = EXCLUDED.status,
                    port = EXCLUDED.port,
                    container = EXCLUDED.container,
                    message = EXCLUDED.message,
                    reported_by = EXCLUDED.reported_by,
                    reported_at = NOW(),
                    expires_at = NOW() + INTERVAL '4 hours'
            """, (args.service, args.status, args.port, args.container, args.message, reported_by))
        conn.commit()
        print("INFRA: [%s] %s — %s" % (args.status.upper(), args.service, args.message or "updated"))
    finally:
        conn.close()


def cmd_project_list(args):
    """List all known projects with memory counts."""
    conn = get_conn_safe()
    if not conn:
        print("CLAMBAKE: Postgres unreachable.")
        return

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT project, COUNT(*) as memories,
                       COUNT(*) FILTER (WHERE status = 'active') as active,
                       MAX(updated_at) as last_updated
                FROM clambake.project_memory
                GROUP BY project
                ORDER BY last_updated DESC
            """)
            rows = cur.fetchall()

            print("=== PROJECTS ===")
            if not rows:
                print("  (no project memories stored)")
                return
            for r in rows:
                ts = r["last_updated"].strftime("%Y-%m-%d") if r["last_updated"] else "?"
                print("  %-25s %d memories (%d active)  updated %s" % (
                    r["project"], r["memories"], r["active"], ts))

            # Also show global memory count
            cur.execute("SELECT COUNT(*) as cnt FROM clambake.global_memory")
            gcnt = cur.fetchone()["cnt"]
            print("\n  Global memories: %d" % gcnt)
    finally:
        conn.close()


# --- Argument Parsing --------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="clambake",
        description="Multi-Instance Claude Code Coordination"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="Initialize clambake schema in Postgres")

    # enable/disable (both long and short forms)
    sub.add_parser("enable", help="Enable Clambake (persists via flag file)")
    sub.add_parser("on", help="Enable Clambake (alias for enable)")
    sub.add_parser("disable", help="Disable Clambake (all commands become no-ops)")
    sub.add_parser("off", help="Disable Clambake (alias for disable)")

    # up (one-command startup)
    p = sub.add_parser("up", help="One-command startup (register + inbox + recall + infra check)")
    p.add_argument("--project", help="Project name (auto-detected from working dir if omitted)")
    p.add_argument("--dir", help="Working directory (defaults to cwd)")
    p.add_argument("--model", default="opus")
    p.add_argument("--role", choices=["boss", "worker", "human"],
                   help="Instance role (falls back to CLAMBAKE_ROLE env var)")

    # down (one-command shutdown)
    p = sub.add_parser("down", help="One-command shutdown (deregister + log)")
    p.add_argument("--summary", help="Optional session summary to log")

    # register (kept for backwards compat)
    p = sub.add_parser("register", help="Register this instance")
    p.add_argument("--project", required=True)
    p.add_argument("--dir")
    p.add_argument("--model", default="opus")
    p.add_argument("--role", choices=["boss", "worker", "human"],
                   help="Instance role (falls back to CLAMBAKE_ROLE env var)")

    # heartbeat
    p = sub.add_parser("heartbeat", help="Update heartbeat")
    p.add_argument("--task")
    p.add_argument("--status", choices=["active", "idle", "busy", "shutting_down"])
    p.add_argument("--role", choices=["boss", "worker", "human"],
                   help="Update instance role (falls back to CLAMBAKE_ROLE env var)")

    # checkin (lightweight heartbeat for hooks)
    sub.add_parser("checkin", help="Lightweight check-in (heartbeat or re-register)")

    # status
    sub.add_parser("status", help="Show all active instances")

    # send
    p = sub.add_parser("send", help="Send a message")
    p.add_argument("--to", required=True, help="instance_id, project, or @all")
    p.add_argument("--subject", required=True)
    p.add_argument("--body")
    p.add_argument("--type", default="info",
                   choices=["info", "warning", "blocker", "request", "done"])

    # inbox
    p = sub.add_parser("inbox", help="Check unread messages")
    p.add_argument("--all", action="store_true", help="Include read messages")

    # read
    p = sub.add_parser("read", help="Read a message")
    p.add_argument("message_id", type=int)

    # remember
    p = sub.add_parser("remember", help="Store knowledge")
    p.add_argument("--project")
    p.add_argument("--global", dest="glob", action="store_true")
    p.add_argument("--type", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--files", help="Comma-separated related file paths")

    # recall
    p = sub.add_parser("recall", help="Query memory")
    p.add_argument("--project")
    p.add_argument("--global", dest="glob", action="store_true")
    p.add_argument("--type")
    p.add_argument("--search")
    p.add_argument("--text-only", dest="text_only", action="store_true",
                   help="Force text search (skip semantic)")
    p.add_argument("--limit", type=int, default=20)

    # update-memory
    p = sub.add_parser("update-memory", help="Update a memory entry")
    p.add_argument("memory_id", type=int)
    p.add_argument("--global", dest="glob", action="store_true")
    p.add_argument("--content")
    p.add_argument("--status", choices=["active", "resolved", "deprecated", "superseded"])
    p.add_argument("--title")

    # log
    p = sub.add_parser("log", help="Log a session action")
    p.add_argument("--action", required=True,
                   choices=["started", "task_started", "task_completed",
                            "issue_found", "issue_resolved", "docker_operation",
                            "file_modified", "shutdown", "idle"])
    p.add_argument("--summary", required=True)
    p.add_argument("--files", help="Comma-separated modified files")

    # --- Task dispatch commands ---

    # task create
    p = sub.add_parser("task-create", help="Create a task")
    p.add_argument("--title", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--description")
    p.add_argument("--role", help="Assigned agent role (coder, qa, reviewer, planner)")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--file-scope", dest="file_scope", help="Comma-separated files this task owns")
    p.add_argument("--depends-on", dest="depends_on", help="Comma-separated task IDs")

    # task list
    p = sub.add_parser("task-list", help="List tasks")
    p.add_argument("--project")
    p.add_argument("--status", choices=["pending", "claimed", "in_progress", "done", "failed"])
    p.add_argument("--role")
    p.add_argument("--available", action="store_true", help="Only show claimable tasks")

    # task claim
    p = sub.add_parser("task-claim", help="Claim a task")
    p.add_argument("task_id", type=int)

    # task done
    p = sub.add_parser("task-done", help="Mark task completed")
    p.add_argument("task_id", type=int)
    p.add_argument("--result", help="Summary of what was done")

    # task fail
    p = sub.add_parser("task-fail", help="Mark task failed")
    p.add_argument("task_id", type=int)
    p.add_argument("--result", help="Reason for failure")

    # --- Agent role commands ---

    # role list
    sub.add_parser("role-list", help="List all agent roles")

    # role get
    p = sub.add_parser("role-get", help="Get role details + system prompt")
    p.add_argument("name")

    # role create
    p = sub.add_parser("role-create", help="Create/update an agent role")
    p.add_argument("--name", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--prompt", required=True, help="System prompt for this role")
    p.add_argument("--capabilities", help="Comma-separated capabilities")
    p.add_argument("--tool-allowlist", dest="tool_allowlist",
                   help="Comma-separated tool allowlist (e.g. Read,Glob,Grep,Edit,Write,Bash)")
    p.add_argument("--tool-denylist", dest="tool_denylist",
                   help="Comma-separated tool denylist")

    # role get-tools (for agent-worker.sh)
    p = sub.add_parser("role-get-tools", help="Get tool allowlist for a role (shell-friendly)")
    p.add_argument("name")
    p.add_argument("--format", default="allowlist", choices=["allowlist", "json", "lines"])

    # role seed
    sub.add_parser("role-seed", help="Seed default roles (8 roles with tool-gating)")

    # --- Pipeline commands ---

    # pipeline seed
    sub.add_parser("pipeline-seed", help="Seed default pipeline templates")

    # pipeline list
    sub.add_parser("pipeline-list", help="List all pipeline templates")

    # pipeline run
    p = sub.add_parser("pipeline-run", help="Create chained tasks from a pipeline template")
    p.add_argument("--pipeline", required=True, help="Template name (e.g. plan-build-review)")
    p.add_argument("--project", required=True, help="Project name")
    p.add_argument("--input", required=True, help="User's request / task description")
    p.add_argument("--priority", type=int, default=0)

    # pipeline status
    p = sub.add_parser("pipeline-status", help="Show pipeline run progress")
    p.add_argument("run_id", nargs="?", help="Specific run ID (omit for all recent runs)")

    # pipeline prev-result (internal, for agent-worker.sh)
    p = sub.add_parser("pipeline-prev-result", help="Get previous step's result (internal)")
    p.add_argument("task_id", type=int)

    # task get-meta (internal, for agent-worker.sh)
    p = sub.add_parser("task-get-meta", help="Get single task field (internal)")
    p.add_argument("task_id", type=int)
    p.add_argument("--field", required=True, help="Field name to retrieve")

    # digest
    p = sub.add_parser("digest", help="Activity summary for last N hours")
    p.add_argument("--hours", type=int, default=24)

    # embed-backfill
    sub.add_parser("embed-backfill", help="Generate embeddings for all memories missing them")

    # deregister
    sub.add_parser("deregister", help="Unregister this instance")

    # cleanup
    sub.add_parser("cleanup", help="Remove stale instances and expired messages")

    # --- Infrastructure commands ---

    # infra (view)
    sub.add_parser("infra", help="Show live infrastructure status")

    # infra-warn (report)
    p = sub.add_parser("infra-warn", help="Report infrastructure status")
    p.add_argument("--service", required=True, help="Service name (e.g. postgres, docker, ollama)")
    p.add_argument("--status", required=True, choices=["up", "down", "degraded", "warning"])
    p.add_argument("--message", help="Human-readable status message")
    p.add_argument("--port", type=int, help="Primary port number")
    p.add_argument("--container", help="Docker container name")

    # project-list
    sub.add_parser("project-list", help="List all known projects with memory counts")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "enable": cmd_enable,
        "on": cmd_enable,
        "disable": cmd_disable,
        "off": cmd_disable,
        "up": cmd_up,
        "down": cmd_down,
        "register": cmd_register,
        "heartbeat": cmd_heartbeat,
        "checkin": cmd_checkin,
        "status": cmd_status,
        "send": cmd_send,
        "inbox": cmd_inbox,
        "read": cmd_read,
        "remember": cmd_remember,
        "recall": cmd_recall,
        "update-memory": cmd_update_memory,
        "log": cmd_log,
        "task-create": cmd_task_create,
        "task-list": cmd_task_list,
        "task-claim": cmd_task_claim,
        "task-done": cmd_task_done,
        "task-fail": cmd_task_fail,
        "role-list": cmd_role_list,
        "role-get": cmd_role_get,
        "role-create": cmd_role_create,
        "role-get-tools": cmd_role_get_tools,
        "role-seed": cmd_role_seed,
        "pipeline-seed": cmd_pipeline_seed,
        "pipeline-list": cmd_pipeline_list,
        "pipeline-run": cmd_pipeline_run,
        "pipeline-status": cmd_pipeline_status,
        "pipeline-prev-result": cmd_pipeline_prev_result,
        "task-get-meta": cmd_task_get_meta,
        "digest": cmd_digest,
        "embed-backfill": cmd_embed_backfill,
        "infra": cmd_infra,
        "infra-warn": cmd_infra_warn,
        "project-list": cmd_project_list,
        "deregister": cmd_deregister,
        "cleanup": cmd_cleanup,
    }

    # Commands that always run regardless of enabled state
    ALWAYS_RUN = {"init", "enable", "on", "disable", "off"}

    # Graceful degradation: these commands use get_conn_safe() internally
    # so they handle Postgres being down without crashing
    GRACEFUL_COMMANDS = {"up", "down", "checkin", "infra", "infra-warn", "project-list",
                         "recall", "remember", "status", "digest"}

    # Gate check: if disabled, silently exit for non-essential commands
    if not CLAMBAKE_ENABLED and args.command not in ALWAYS_RUN:
        sys.exit(0)

    try:
        commands[args.command](args)
    except psycopg2.OperationalError as e:
        if args.command in GRACEFUL_COMMANDS:
            print("CLAMBAKE: Postgres unreachable (localhost:%s). Skipping." % DB_PORT)
        else:
            print("DB ERROR: %s" % e)
            print("Is Postgres running? (docker start postgres)")
            sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
