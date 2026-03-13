#!/usr/bin/env python3
"""
Clambake — Memory, Infrastructure & Audit Layer for Claude Code

A lightweight CLI for persistent project/global memory (Postgres + pgvector),
infrastructure status tracking, inter-instance messaging, and session audit trails.

Orchestration (task dispatch, role management, pipelines) has been removed.
Agent Teams in Claude Code handles execution; Clambake handles knowledge.

Usage:
    clambake up [--project <name>] [--dir <path>]  # One-command startup
    clambake down                                    # One-command shutdown
    clambake remember --project <name> --type <type> --title <text> --content <text>
    clambake recall --project <name> [--search <query>]
    clambake recall --global [--search <query>]
    clambake status                                  # Active instances + recent messages
    clambake send --to <target> --subject <text>     # Inter-instance messaging
    clambake infra                                   # Infrastructure status
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

CLAMBAKE_ENABLED = os.environ.get("CLAMBAKE_ENABLED", "1") == "1"
CLAMBAKE_FLAG_FILE = Path(os.environ.get(
    "CLAMBAKE_FLAG_FILE",
    Path.home() / ".clambake_enabled"
))

if CLAMBAKE_FLAG_FILE.exists():
    CLAMBAKE_ENABLED = CLAMBAKE_FLAG_FILE.read_text().strip() == "1"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.environ.get("CLAMBAKE_EMBEDDING_MODEL", "nomic-embed-text")

DB_HOST = os.environ.get("CLAMBAKE_DB_HOST", "localhost")
DB_PORT = os.environ.get("CLAMBAKE_DB_PORT", "5433")
DB_NAME = os.environ.get("CLAMBAKE_DB_NAME", "docdb")
DB_USER = os.environ.get("CLAMBAKE_DB_USER", "postgres")
DB_PASS = os.environ.get("CLAMBAKE_DB_PASS", "postgres")

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

PROJECT_OVERRIDES = {}


def _normalize_name(name):
    """Normalize a directory name into a project slug (lowercase, hyphens)."""
    return name.strip().lower().replace(" ", "-")


def detect_project(working_dir=None):
    """Auto-detect project name from working directory."""
    d = (working_dir or os.getcwd()).replace("\\", "/")
    for prefix, project in PROJECT_OVERRIDES.items():
        if d.startswith(prefix):
            return project
    name = Path(d).name
    if not name:
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
            subprocess.Popen(
                [r"C:\Program Files\Docker\Docker\resources\com.docker.backend.exe",
                 "-with-frontend=false"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            for _ in range(20):
                time.sleep(3)
                if _docker_ready():
                    break
            else:
                return False

        for _ in range(3):
            result = subprocess.run(
                ["docker", "start", "postgres"], capture_output=True, timeout=15
            )
            if result.returncode == 0:
                break
            time.sleep(3)
        else:
            return False

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
        embeddings = data.get("embeddings")
        if embeddings and len(embeddings) > 0:
            return embeddings[0]
        return []
    except Exception:
        return []


# --- Cleanup -----------------------------------------------------------------

def _run_cleanup(conn):
    """Run cleanup and return counts dict. Caller must commit."""
    with conn.cursor() as cur:
        cur.execute("SELECT clambake.cleanup()")
        result = cur.fetchone()[0]
    conn.commit()
    if isinstance(result, str):
        return json.loads(result)
    return result or {}


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


def cmd_up(args):
    """One-command session startup: register + inbox + recall + infra check."""
    conn = get_conn_safe()
    if not conn:
        print("CLAMBAKE: Postgres unreachable (localhost:%s). Running without coordination." % DB_PORT)
        return

    try:
        working_dir = args.dir or os.getcwd()
        project = args.project or detect_project(working_dir)
        instance_id = str(uuid.uuid4())[:12]
        model = args.model or "opus"

        # Auto-cleanup stale data
        counts = _run_cleanup(conn)
        cleaned = sum(v for v in counts.values() if isinstance(v, int))

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Register
            cur.execute("""
                INSERT INTO clambake.instances
                    (instance_id, project, working_dir, model, status)
                VALUES (%s, %s, %s, %s, 'active')
                ON CONFLICT (instance_id) DO UPDATE SET
                    last_heartbeat = NOW(), status = 'active'
            """, (instance_id, project, working_dir, model))
            # Log session start
            cur.execute("""
                INSERT INTO clambake.session_log
                    (instance_id, project, action, summary)
                VALUES (%s, %s, 'started', 'Session started')
            """, (instance_id, project))
        conn.commit()
        save_instance_id(instance_id, project)

        print("=== CLAMBAKE UP ===")
        print("  Instance: %s | Project: %s" % (instance_id, project))
        if cleaned > 0:
            print("  Auto-cleaned %d stale entries" % cleaned)

        # Other active instances
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT instance_id, project, current_task, status
                FROM clambake.active_instances
                WHERE instance_id != %s
            """, (instance_id,))
            others = cur.fetchall()
            if others:
                print("\n--- Active Instances ---")
                for o in others:
                    task = o["current_task"] or "idle"
                    print("  [%s] %s \u2014 %s (%s)" % (
                        o["status"], o["project"], task, o["instance_id"]))

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
                    print("  [%s] %s \u2014 %s" % (m["message_type"], proj, m["subject"]))

            # Infrastructure warnings
            cur.execute("""
                SELECT service, status, message FROM clambake.current_infra
                WHERE status IN ('down', 'degraded', 'warning')
            """)
            warnings = cur.fetchall()
            if warnings:
                print("\n--- [!] Infrastructure Warnings ---")
                for w in warnings:
                    print("  [%s] %s \u2014 %s" % (w["status"].upper(), w["service"], w["message"] or ""))

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
                    print("  #%d [%s] %s" % (m["id"], m["memory_type"], m["title"]))

            # Global warnings/lessons
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


def cmd_checkin(args):
    """Lightweight check-in: heartbeat if registered, re-register if not."""
    conn = get_conn_safe()
    if not conn:
        return

    try:
        instance_id, project = get_instance_id()

        if instance_id:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE clambake.instances
                    SET last_heartbeat = NOW(), status = 'active'
                    WHERE instance_id = %s
                    RETURNING instance_id
                """, (instance_id,))
                row = cur.fetchone()

                if row:
                    conn.commit()
                    print("CHECKIN: %s (%s)" % (instance_id, project))
                    return
                else:
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

        # No instance file — first prompt of a new session
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
            cur.execute("SELECT * FROM clambake.active_instances")
            instances = cur.fetchall()

            print("=== ACTIVE INSTANCES ===")
            if not instances:
                print("  (none)")
            for i in instances:
                task = i["current_task"] or "idle"
                age = i["seconds_since_heartbeat"]
                age_str = "%ds ago" % age if age < 60 else "%dm ago" % (age // 60)
                print("  [%s] %s \u2014 %s (%s) %s" % (
                    i["status"], i["project"], task, age_str, i["instance_id"]))

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
                print("  %s [%s] %s \u2014 %s" % (
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
        print("SENT: [%s] #%d to %s \u2014 %s" % (args.type, msg_id, args.to, args.subject))
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
                print("  %s#%d [%s] %s from %s (%s) \u2014 %s" % (
                    read_mark, m["id"], m["message_type"],
                    ts, proj, m["from_instance"][:8], m["subject"]))
                if m["body"]:
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

    embed_text = args.title + "\n" + args.content
    embedding = generate_embedding(embed_text)
    embed_status = "(embedded)" if embedding else "(text only)"

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if args.glob:
                cur.execute("""
                    INSERT INTO clambake.global_memory
                        (memory_type, title, content, tags, created_by, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (args.type, args.title, args.content, tags, created_by,
                      embedding if embedding else None))
            else:
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
            if instance_id:
                scope_name = "global" if args.glob else args.project
                cur.execute("""
                    INSERT INTO clambake.session_log
                        (instance_id, project, action, summary)
                    VALUES (%s, %s, 'task_completed', %s)
                """, (instance_id, scope_name or "",
                      "Stored %s memory #%d: %s" % (args.type, mem_id, args.title)))
        conn.commit()
        scope = "global" if args.glob else args.project
        print("REMEMBERED: #%d [%s] in %s \u2014 %s %s" % (
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
            use_semantic = False

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if use_semantic:
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

                # Fallback for entries without embeddings
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
                content = r["content"][:300]
                if len(r["content"]) > 300:
                    content += "..."
                print("    %s" % content)

                if r.get("related_files"):
                    print("    files: %s" % ", ".join(r["related_files"]))
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
                    print("WARNING: --status is not supported for global memory. Ignoring.")
                else:
                    updates.append("status = %s")
                    params.append(args.status)
            if args.title:
                updates.append("title = %s")
                params.append(args.title)

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


def cmd_digest(args):
    """Show activity summary for last N hours."""
    hours = args.hours
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            print("=== CLAMBAKE DIGEST (last %dh) ===" % hours)

            cur.execute("SELECT * FROM clambake.active_instances")
            instances = cur.fetchall()
            print("\n--- Active Instances ---")
            if not instances:
                print("  (none)")
            for i in instances:
                task = i["current_task"] or "idle"
                age = i["seconds_since_heartbeat"]
                stale = " [!] STALE" if age > 300 else ""
                print("  [%s] %s \u2014 %s (heartbeat %ds ago)%s" % (
                    i["status"], i["project"], task, age, stale))

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
                        print("  OK: %s #%d \u2014 %s" % (table.split(".")[-1], r["id"], r["title"][:60]))
                    else:
                        fail += 1
                        print("  FAIL: %s #%d \u2014 %s" % (table.split(".")[-1], r["id"], r["title"][:60]))
            conn.commit()

        print("\nBACKFILL: %d total, %d embedded, %d failed" % (total, success, fail))
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
                print("  (no services reported \u2014 use 'clambake infra-warn' to report)")
                return
            for r in rows:
                status = r["status"].upper()
                marker = "  " if r["status"] == "up" else "[!]"
                port_str = ":%d" % r["port"] if r["port"] else ""
                container_str = " (%s)" % r["container"] if r["container"] else ""
                ttl = r["seconds_until_expiry"]
                ttl_str = " [expires %dm]" % (ttl // 60) if ttl < 3600 else ""
                msg = " \u2014 %s" % r["message"] if r["message"] else ""
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
        print("INFRA: [%s] %s \u2014 %s" % (args.status.upper(), args.service, args.message or "updated"))
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

            cur.execute("SELECT COUNT(*) as cnt FROM clambake.global_memory")
            gcnt = cur.fetchone()["cnt"]
            print("\n  Global memories: %d" % gcnt)
    finally:
        conn.close()


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


def cmd_disable(args):
    """Disable Clambake coordination."""
    CLAMBAKE_FLAG_FILE.write_text("0")
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
    print("  Re-enable with: clambake enable")


def cmd_deregister(args):
    """Mark this instance as gone."""
    instance_id, project = get_instance_id()
    if not instance_id:
        print("Not registered.")
        return

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.session_log
                    (instance_id, project, action, summary)
                VALUES (%s, %s, 'shutdown', 'Session ended')
            """, (instance_id, project))
            cur.execute(
                "DELETE FROM clambake.instances WHERE instance_id = %s",
                (instance_id,)
            )
        conn.commit()
        clear_instance_id()
        print("DEREGISTERED: %s" % instance_id)
    finally:
        conn.close()


# --- Argument Parsing --------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="clambake",
        description="Clambake \u2014 Memory, Infrastructure & Audit Layer for Claude Code"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="Initialize clambake schema in Postgres")

    # enable/disable
    sub.add_parser("enable", help="Enable Clambake")
    sub.add_parser("on", help="Enable Clambake (alias)")
    sub.add_parser("disable", help="Disable Clambake")
    sub.add_parser("off", help="Disable Clambake (alias)")

    # up
    p = sub.add_parser("up", help="One-command startup")
    p.add_argument("--project", help="Project name (auto-detected if omitted)")
    p.add_argument("--dir", help="Working directory (defaults to cwd)")
    p.add_argument("--model", default="opus")

    # down
    p = sub.add_parser("down", help="One-command shutdown")
    p.add_argument("--summary", help="Optional session summary")

    # checkin
    sub.add_parser("checkin", help="Lightweight check-in (heartbeat or re-register)")

    # status
    sub.add_parser("status", help="Show active instances and recent messages")

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

    # digest
    p = sub.add_parser("digest", help="Activity summary for last N hours")
    p.add_argument("--hours", type=int, default=24)

    # embed-backfill
    sub.add_parser("embed-backfill", help="Generate embeddings for memories missing them")

    # deregister
    sub.add_parser("deregister", help="Unregister this instance")

    # cleanup
    sub.add_parser("cleanup", help="Remove stale instances and expired messages")

    # infra
    sub.add_parser("infra", help="Show live infrastructure status")

    # infra-warn
    p = sub.add_parser("infra-warn", help="Report infrastructure status")
    p.add_argument("--service", required=True)
    p.add_argument("--status", required=True, choices=["up", "down", "degraded", "warning"])
    p.add_argument("--message", help="Human-readable status message")
    p.add_argument("--port", type=int)
    p.add_argument("--container", help="Docker container name")

    # project-list
    sub.add_parser("project-list", help="List all projects with memory counts")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "enable": cmd_enable,
        "on": cmd_enable,
        "disable": cmd_disable,
        "off": cmd_disable,
        "up": cmd_up,
        "down": cmd_down,
        "checkin": cmd_checkin,
        "status": cmd_status,
        "send": cmd_send,
        "inbox": cmd_inbox,
        "read": cmd_read,
        "remember": cmd_remember,
        "recall": cmd_recall,
        "update-memory": cmd_update_memory,
        "log": cmd_log,
        "digest": cmd_digest,
        "embed-backfill": cmd_embed_backfill,
        "infra": cmd_infra,
        "infra-warn": cmd_infra_warn,
        "project-list": cmd_project_list,
        "deregister": cmd_deregister,
        "cleanup": cmd_cleanup,
    }

    ALWAYS_RUN = {"init", "enable", "on", "disable", "off"}
    GRACEFUL_COMMANDS = {"up", "down", "checkin", "infra", "infra-warn", "project-list",
                         "recall", "remember", "status", "digest"}

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
