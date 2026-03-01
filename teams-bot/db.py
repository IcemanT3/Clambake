"""Database helpers for Teams bot."""
import json
import psycopg2
import psycopg2.extras
from config import Config


def get_conn():
    return psycopg2.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, dbname=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASS
    )


# --- Conversation references (for proactive messaging) ---

def save_conversation_reference(ref_dict, conversation_type="channel",
                                user_id=None, user_name=None):
    """Store a Teams conversation reference for proactive messaging."""
    conv_id = ref_dict.get("conversation", {}).get("id", "")
    service_url = ref_dict.get("serviceUrl", "")
    channel_id = ref_dict.get("channelId", "")
    bot_id = ref_dict.get("bot", {}).get("id", "")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.conversation_references
                    (conversation_id, conversation_type, service_url,
                     channel_id, user_id, user_name, bot_id, reference_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (conversation_id, user_id) DO UPDATE SET
                    service_url = EXCLUDED.service_url,
                    reference_json = EXCLUDED.reference_json,
                    updated_at = NOW()
            """, (conv_id, conversation_type, service_url,
                  channel_id, user_id, user_name, bot_id,
                  json.dumps(ref_dict)))
        conn.commit()
    finally:
        conn.close()


def get_channel_references():
    """Get all channel conversation references."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT ON (conversation_id) *
                FROM clambake.conversation_references
                WHERE conversation_type = 'channel'
                ORDER BY conversation_id, updated_at DESC
            """)
            return cur.fetchall()
    finally:
        conn.close()


def get_personal_references():
    """Get all personal (DM) conversation references."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM clambake.conversation_references
                WHERE conversation_type = 'personal'
                ORDER BY updated_at DESC
            """)
            return cur.fetchall()
    finally:
        conn.close()


# --- Notification dedup ---

def is_already_notified(event_type, event_source_id):
    """Check if we already sent a notification for this event."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM clambake.notification_log
                WHERE event_type = %s AND event_source_id = %s
            """, (event_type, str(event_source_id)))
            return cur.fetchone() is not None
    finally:
        conn.close()


def mark_notified(event_type, event_source_id):
    """Record that we sent a notification."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.notification_log (event_type, event_source_id)
                VALUES (%s, %s)
                ON CONFLICT (event_type, event_source_id) DO NOTHING
            """, (event_type, str(event_source_id)))
        conn.commit()
    finally:
        conn.close()


# --- Query helpers (reused by bot commands) ---

def get_active_instances():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM clambake.active_instances")
            return cur.fetchall()
    finally:
        conn.close()


def get_tasks(project=None, status=None):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = "SELECT * FROM clambake.tasks WHERE TRUE"
            params = []
            if project:
                query += " AND project = %s"
                params.append(project)
            if status:
                query += " AND status = %s"
                params.append(status)
            query += " ORDER BY priority DESC, created_at ASC LIMIT 50"
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        conn.close()


def get_digest(hours=24):
    """Get digest data (mirrors cmd_digest logic)."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            result = {}

            cur.execute("SELECT * FROM clambake.active_instances")
            result["instances"] = cur.fetchall()

            cur.execute("""
                SELECT status, COUNT(*) as cnt FROM clambake.tasks
                WHERE created_at > NOW() - INTERVAL '%s hours'
                   OR status IN ('pending', 'claimed', 'in_progress')
                GROUP BY status
            """, (hours,))
            result["task_counts"] = {r["status"]: r["cnt"] for r in cur.fetchall()}

            cur.execute("""
                SELECT id, title, project, completed_at FROM clambake.tasks
                WHERE status = 'done' AND completed_at > NOW() - INTERVAL '%s hours'
                ORDER BY completed_at DESC LIMIT 10
            """, (hours,))
            result["done_tasks"] = cur.fetchall()

            cur.execute("""
                SELECT id, from_project, subject FROM clambake.messages
                WHERE message_type = 'blocker' AND NOT is_read
                  AND created_at > NOW() - INTERVAL '%s hours'
            """, (hours,))
            result["blockers"] = cur.fetchall()

            return result
    finally:
        conn.close()


def search_memory(query, limit=10):
    """Text search across project and global memory."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            pattern = "%%%s%%" % query
            cur.execute("""
                (SELECT id, 'project' AS scope, project, memory_type, title,
                        LEFT(content, 200) AS preview
                 FROM clambake.project_memory
                 WHERE status = 'active' AND (title ILIKE %s OR content ILIKE %s)
                 ORDER BY updated_at DESC LIMIT %s)
                UNION ALL
                (SELECT id, 'global' AS scope, NULL AS project, memory_type, title,
                        LEFT(content, 200) AS preview
                 FROM clambake.global_memory
                 WHERE title ILIKE %s OR content ILIKE %s
                 ORDER BY updated_at DESC LIMIT %s)
            """, (pattern, pattern, limit, pattern, pattern, limit))
            return cur.fetchall()
    finally:
        conn.close()


def send_message(from_instance, from_project, to_target, message_type, subject, body=None):
    """Send a Clambake message."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clambake.messages
                    (from_instance, from_project, to_target, message_type, subject, body)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (from_instance, from_project, to_target, message_type, subject, body))
            msg_id = cur.fetchone()[0]
        conn.commit()
        return msg_id
    finally:
        conn.close()


# --- Events for proactive notifications ---

def get_new_events(lookback_minutes=5):
    """Get recent events that may need notification."""
    conn = get_conn()
    try:
        events = []
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Completed tasks
            cur.execute("""
                SELECT id, title, project, assigned_role, result
                FROM clambake.tasks
                WHERE status = 'done'
                  AND completed_at > NOW() - INTERVAL '%s minutes'
            """, (lookback_minutes,))
            for t in cur.fetchall():
                events.append({
                    "type": "task_completed",
                    "source_id": str(t["id"]),
                    "targets": ["channel"],
                    "text": "Task #%d completed: **%s** (%s)" % (
                        t["id"], t["title"], t["project"])
                })

            # Failed tasks
            cur.execute("""
                SELECT id, title, project, result
                FROM clambake.tasks
                WHERE status = 'failed'
                  AND completed_at > NOW() - INTERVAL '%s minutes'
            """, (lookback_minutes,))
            for t in cur.fetchall():
                events.append({
                    "type": "task_failed",
                    "source_id": str(t["id"]),
                    "targets": ["channel", "dm"],
                    "text": "Task #%d FAILED: **%s** (%s) — %s" % (
                        t["id"], t["title"], t["project"], t.get("result") or "no reason")
                })

            # New registrations
            cur.execute("""
                SELECT instance_id, project, model
                FROM clambake.instances
                WHERE started_at > NOW() - INTERVAL '%s minutes'
            """, (lookback_minutes,))
            for i in cur.fetchall():
                events.append({
                    "type": "agent_registered",
                    "source_id": i["instance_id"],
                    "targets": ["channel"],
                    "text": "Agent registered: **%s** on %s (%s)" % (
                        i["instance_id"], i["project"], i["model"] or "?")
                })

            # Blocker messages
            cur.execute("""
                SELECT id, from_project, subject, body
                FROM clambake.messages
                WHERE message_type = 'blocker'
                  AND created_at > NOW() - INTERVAL '%s minutes'
            """, (lookback_minutes,))
            for m in cur.fetchall():
                events.append({
                    "type": "blocker",
                    "source_id": str(m["id"]),
                    "targets": ["channel", "dm"],
                    "text": "BLOCKER from %s: **%s**%s" % (
                        m["from_project"] or "?", m["subject"],
                        "\n> %s" % m["body"][:200] if m.get("body") else "")
                })

            # Stale instances (no heartbeat > 5 min)
            cur.execute("""
                SELECT instance_id, project,
                       EXTRACT(EPOCH FROM (NOW() - last_heartbeat))::int AS age
                FROM clambake.instances
                WHERE last_heartbeat < NOW() - INTERVAL '5 minutes'
                  AND last_heartbeat > NOW() - INTERVAL '6 minutes'
            """)
            for s in cur.fetchall():
                events.append({
                    "type": "stale_instance",
                    "source_id": s["instance_id"],
                    "targets": ["dm"],
                    "text": "Stale agent: **%s** (%s) — no heartbeat for %ds" % (
                        s["project"], s["instance_id"], s["age"])
                })

        return events
    finally:
        conn.close()
