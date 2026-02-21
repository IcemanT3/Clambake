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


def cmd_role_list(args):
    """List all agent roles."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT name, description, capabilities FROM clambake.agent_roles ORDER BY name")
            roles = cur.fetchall()
            if not roles:
                print("ROLES: none defined. Run 'clambake role seed' to create defaults.")
                return
            print("=== AGENT ROLES ===")
            for r in roles:
                caps = ", ".join(r["capabilities"] or [])
                print("  [%s] %s  (%s)" % (r["name"], r["description"], caps))
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
            print("  System Prompt:\n%s" % r["system_prompt"])
    finally:
        conn.close()


def cmd_role_create(args):
    """Create or update an agent role."""
    caps = [c.strip() for c in args.capabilities.split(",")] if args.capabilities else []
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.agent_roles (name, description, system_prompt, capabilities)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    description = EXCLUDED.description,
                    system_prompt = EXCLUDED.system_prompt,
                    capabilities = EXCLUDED.capabilities,
                    updated_at = NOW()
            """, (args.name, args.description, args.prompt, caps))
        conn.commit()
        print("ROLE: '%s' saved" % args.name)
    finally:
        conn.close()


def cmd_role_seed(args):
    """Seed the four default agent roles."""
    roles = [
        {
            "name": "planner",
            "description": "Reads codebase, designs architecture, writes specs. Does not code.",
            "system_prompt": (
                "You are the Planner. Your job is to read the codebase, understand the architecture, "
                "and write detailed implementation specs for other agents.\n\n"
                "RULES:\n"
                "- Read and analyze code extensively before writing a spec\n"
                "- Break large tasks into discrete, non-overlapping subtasks\n"
                "- Each subtask should specify: files to create/modify, expected behavior, acceptance criteria\n"
                "- Assign a file_scope to each subtask so agents don't conflict\n"
                "- DO NOT write code — only specs and plans\n"
                "- Use 'clambake task create' to dispatch subtasks when your plan is ready\n"
                "- Use 'clambake remember' to store architecture decisions"
            ),
            "capabilities": ["read_code", "write_specs", "create_tasks"]
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
                "- When done, run 'clambake task done <id>' with a summary of what you built\n"
                "- If blocked, run 'clambake task fail <id> --result \"reason\"' and it will be reassigned"
            ),
            "capabilities": ["write_code", "read_code"]
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
                "- If you find bugs, use 'clambake task create' to file a bug fix task for the coder\n"
                "- DO NOT fix bugs yourself — report them\n"
                "- When all tests pass, run 'clambake task done <id>' with test results\n"
                "- Use 'clambake send' to notify the coder of any issues found"
            ),
            "capabilities": ["read_code", "write_tests", "run_tests", "create_tasks"]
        },
        {
            "name": "reviewer",
            "description": "Reviews code for quality, security, and patterns. Approves or rejects.",
            "system_prompt": (
                "You are the Reviewer. You review code changes for quality and correctness.\n\n"
                "RULES:\n"
                "- Read the task spec and the code that was written\n"
                "- Check for: correctness, security issues, code quality, adherence to patterns\n"
                "- If approved, run 'clambake task done <id>' with your review notes\n"
                "- If rejected, run 'clambake task fail <id>' with specific feedback\n"
                "- DO NOT modify code yourself — only review and provide feedback\n"
                "- Use 'clambake remember' to document patterns you want enforced"
            ),
            "capabilities": ["read_code", "review"]
        }
    ]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for r in roles:
                cur.execute("""
                    INSERT INTO clambake.agent_roles (name, description, system_prompt, capabilities)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        description = EXCLUDED.description,
                        system_prompt = EXCLUDED.system_prompt,
                        capabilities = EXCLUDED.capabilities,
                        updated_at = NOW()
                """, (r["name"], r["description"], r["system_prompt"], r["capabilities"]))
        conn.commit()
        print("SEEDED: %d agent roles (planner, coder, qa, reviewer)" % len(roles))
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

    # enable/disable (both long and short forms)
    sub.add_parser("enable", help="Enable Clambake (persists via flag file)")
    sub.add_parser("on", help="Enable Clambake (alias for enable)")
    sub.add_parser("disable", help="Disable Clambake (all commands become no-ops)")
    sub.add_parser("off", help="Disable Clambake (alias for disable)")

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

    # role seed
    sub.add_parser("role-seed", help="Seed default roles (planner, coder, qa, reviewer)")

    # deregister
    sub.add_parser("deregister", help="Unregister this instance")

    # cleanup
    sub.add_parser("cleanup", help="Remove stale instances and expired messages")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "enable": cmd_enable,
        "on": cmd_enable,
        "disable": cmd_disable,
        "off": cmd_disable,
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
        "task-create": cmd_task_create,
        "task-list": cmd_task_list,
        "task-claim": cmd_task_claim,
        "task-done": cmd_task_done,
        "task-fail": cmd_task_fail,
        "role-list": cmd_role_list,
        "role-get": cmd_role_get,
        "role-create": cmd_role_create,
        "role-seed": cmd_role_seed,
        "deregister": cmd_deregister,
        "cleanup": cmd_cleanup,
    }

    # Commands that always run regardless of enabled state
    ALWAYS_RUN = {"init", "enable", "on", "disable", "off"}

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
