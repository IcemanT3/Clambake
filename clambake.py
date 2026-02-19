#!/usr/bin/env python3
"""
Clambake — Multi-Instance Claude Code Coordination via Postgres

A lightweight CLI that Claude Code instances use to coordinate work
across projects through a shared Postgres database.

Usage:
    clambake register --project <name> [--dir <path>] [--model <model>]
    clambake heartbeat [--task <description>] [--status <status>]
    clambake status
    clambake send --to <target> --subject <text> [--body <text>] [--type <type>]
    clambake inbox [--all]
    clambake read <message_id>
    clambake remember --project <name> --type <type> --title <text> --content <text> [--tags <t1,t2>]
    clambake recall --project <name> [--type <type>] [--search <query>] [--limit <n>]
    clambake recall --global [--type <type>] [--search <query>]
    clambake log --action <action> --summary <text> [--files <f1,f2>]
    clambake deregister
    clambake cleanup
    clambake init
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras

# --- Configuration -----------------------------------------------------------

# Master switch: set CLAMBAKE_ENABLED=1 to activate, 0 or unset to disable.
# When disabled, all commands silently exit 0 (no output, no errors).
# The 'enable', 'disable', and 'init' commands always run regardless.
CLAMBAKE_ENABLED = os.environ.get("CLAMBAKE_ENABLED", "0") == "1"
CLAMBAKE_FLAG_FILE = Path(os.environ.get(
    "CLAMBAKE_FLAG_FILE",
    Path.home() / ".clambake_enabled"
))

# Also check flag file (survives shell restarts without .bashrc editing)
if not CLAMBAKE_ENABLED and CLAMBAKE_FLAG_FILE.exists():
    CLAMBAKE_ENABLED = CLAMBAKE_FLAG_FILE.read_text().strip() == "1"

DB_HOST = os.environ.get("CLAMBAKE_DB_HOST", "localhost")
DB_PORT = os.environ.get("CLAMBAKE_DB_PORT", "5433")
DB_NAME = os.environ.get("CLAMBAKE_DB_NAME", "docdb")
DB_USER = os.environ.get("CLAMBAKE_DB_USER", "postgres")
DB_PASS = os.environ.get("CLAMBAKE_DB_PASS", "postgres")

# Instance ID persists for the session, stored in a temp file
INSTANCE_FILE = Path(os.environ.get(
    "CLAMBAKE_INSTANCE_FILE",
    Path.home() / ".clambake_instance"
))


def get_conn():
    """Get a database connection."""
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS
    )


def get_instance_id():
    """Read current instance ID from file, or None."""
    if INSTANCE_FILE.exists():
        data = json.loads(INSTANCE_FILE.read_text())
        return data.get("instance_id"), data.get("project")
    return None, None


def save_instance_id(instance_id, project):
    """Save instance ID to file."""
    INSTANCE_FILE.write_text(json.dumps({
        "instance_id": instance_id,
        "project": project
    }))


def clear_instance_id():
    """Remove instance ID file."""
    if INSTANCE_FILE.exists():
        INSTANCE_FILE.unlink()


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

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.instances
                    (instance_id, project, working_dir, model, status)
                VALUES (%s, %s, %s, %s, 'active')
                ON CONFLICT (instance_id) DO UPDATE SET
                    last_heartbeat = NOW(), status = 'active'
            """, (instance_id, project, working_dir, model))
        conn.commit()
        save_instance_id(instance_id, project)
        print("REGISTERED: %s on project '%s'" % (instance_id, project))

        # Check for other active instances and unread messages
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT instance_id, project, current_task, status
                FROM clambake.active_instances
                WHERE instance_id != %s
            """, (instance_id,))
            others = cur.fetchall()
            if others:
                print("\nACTIVE INSTANCES:")
                for o in others:
                    task = o["current_task"] or "idle"
                    print("  [%s] %s — %s (%s)" % (
                        o["status"], o["project"], task, o["instance_id"]))

            # Check for messages to this project or @all
            cur.execute("""
                SELECT COUNT(*) as cnt FROM clambake.unread_messages
                WHERE to_target IN (%s, %s, '@all')
            """, (instance_id, project))
            msg_count = cur.fetchone()["cnt"]
            if msg_count:
                print("\n%d UNREAD MESSAGE(S) — run 'clambake inbox'" % msg_count)
    finally:
        conn.close()


def cmd_heartbeat(args):
    """Update heartbeat and optionally current task/status."""
    instance_id, project = get_instance_id()
    if not instance_id:
        print("ERROR: Not registered. Run 'clambake register' first.")
        sys.exit(1)

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
            params.append(instance_id)

            cur.execute(
                "UPDATE clambake.instances SET %s WHERE instance_id = %%s"
                % ", ".join(updates), params
            )
        conn.commit()
        task_msg = " task='%s'" % args.task if args.task else ""
        print("HEARTBEAT: %s%s" % (instance_id, task_msg))
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
                print("  [%s] %s — %s (heartbeat %ds ago) %s" % (
                    i["status"], i["project"], task, age, i["instance_id"]))

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

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if args.glob:
                # Global memory
                cur.execute("""
                    INSERT INTO clambake.global_memory
                        (memory_type, title, content, tags, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (args.type, args.title, args.content, tags, created_by))
            else:
                # Project memory
                cur.execute("""
                    INSERT INTO clambake.project_memory
                        (project, memory_type, title, content, tags,
                         related_files, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (args.project, args.type, args.title, args.content,
                      tags, files, created_by))
            mem_id = cur.fetchone()[0]
        conn.commit()
        scope = "global" if args.glob else args.project
        print("REMEMBERED: #%d [%s] in %s — %s" % (
            mem_id, args.type, scope, args.title))
    finally:
        conn.close()


def cmd_recall(args):
    """Query project or global memory."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if args.glob:
                # Global memory
                query = "SELECT * FROM clambake.global_memory WHERE TRUE"
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
                # Project memory
                query = """
                    SELECT * FROM clambake.project_memory
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
            print("RECALL [%s]: %d result(s)" % (scope, len(rows)))
            for r in rows:
                tags_str = " ".join("#%s" % t for t in (r.get("tags") or []))
                status = r.get("status", "")
                status_str = " (%s)" % status if status and status != "active" else ""
                print("\n  #%d [%s]%s %s %s" % (
                    r["id"], r["memory_type"], status_str, r["title"], tags_str))
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


def cmd_cleanup(args):
    """Run cleanup to remove stale data."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT clambake.cleanup()")
        conn.commit()
        print("CLEANUP: done")
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


def cmd_update_memory(args):
    """Update an existing memory entry."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            updates = ["updated_at = NOW()"]
            params = []
            if args.content:
                updates.append("content = %s")
                params.append(args.content)
            if args.status:
                updates.append("status = %s")
                params.append(args.status)
            if args.title:
                updates.append("title = %s")
                params.append(args.title)
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


# --- Argument Parsing --------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="clambake",
        description="Multi-Instance Claude Code Coordination"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="Initialize clambake schema in Postgres")

    # enable/disable
    sub.add_parser("enable", help="Enable Clambake (persists via flag file)")
    sub.add_parser("disable", help="Disable Clambake (all commands become no-ops)")

    # register
    p = sub.add_parser("register", help="Register this instance")
    p.add_argument("--project", required=True)
    p.add_argument("--dir")
    p.add_argument("--model", default="opus")

    # heartbeat
    p = sub.add_parser("heartbeat", help="Update heartbeat")
    p.add_argument("--task")
    p.add_argument("--status", choices=["active", "idle", "busy", "shutting_down"])

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
                            "file_modified", "shutdown"])
    p.add_argument("--summary", required=True)
    p.add_argument("--files", help="Comma-separated modified files")

    # deregister
    sub.add_parser("deregister", help="Unregister this instance")

    # cleanup
    sub.add_parser("cleanup", help="Remove stale instances and expired messages")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "register": cmd_register,
        "heartbeat": cmd_heartbeat,
        "status": cmd_status,
        "send": cmd_send,
        "inbox": cmd_inbox,
        "read": cmd_read,
        "remember": cmd_remember,
        "recall": cmd_recall,
        "update-memory": cmd_update_memory,
        "log": cmd_log,
        "deregister": cmd_deregister,
        "cleanup": cmd_cleanup,
    }

    # Commands that always run regardless of enabled state
    ALWAYS_RUN = {"init", "enable", "disable"}

    # Gate check: if disabled, silently exit for non-essential commands
    if not CLAMBAKE_ENABLED and args.command not in ALWAYS_RUN:
        sys.exit(0)

    try:
        commands[args.command](args)
    except psycopg2.OperationalError as e:
        print("DB ERROR: %s" % e)
        print("Is Postgres running? (docker start swarm-postgres)")
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
