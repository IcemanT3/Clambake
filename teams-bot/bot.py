"""Teams bot command handler."""
import json
from microsoft_agents.botbuilder.core import ActivityHandler, TurnContext
from microsoft_agents.connector.models import Activity

import db


class ClambakeBot(ActivityHandler):
    """Handles inbound messages from Teams."""

    async def on_message_activity(self, turn_context: TurnContext):
        text = (turn_context.activity.text or "").strip()

        # Strip bot mention if present
        if turn_context.activity.entities:
            for entity in turn_context.activity.entities:
                if entity.type == "mention":
                    mention_text = getattr(entity, "text", "")
                    if mention_text:
                        text = text.replace(mention_text, "").strip()

        # Save conversation reference for proactive messaging
        ref = TurnContext.get_conversation_reference(turn_context.activity)
        ref_dict = ref.as_dict() if hasattr(ref, "as_dict") else json.loads(json.dumps(ref, default=str))
        conv_type = "personal" if turn_context.activity.conversation.conversation_type == "personal" else "channel"
        user = turn_context.activity.from_property
        db.save_conversation_reference(
            ref_dict,
            conversation_type=conv_type,
            user_id=getattr(user, "id", None),
            user_name=getattr(user, "name", None),
        )

        # Route commands
        if text.startswith("/"):
            parts = text.split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            response = await self._handle_command(cmd, arg)
        else:
            response = self._help_text()

        await turn_context.send_activity(Activity(type="message", text=response))

    async def on_conversation_update_activity(self, turn_context: TurnContext):
        """Save reference when bot is added to a channel."""
        if turn_context.activity.members_added:
            ref = TurnContext.get_conversation_reference(turn_context.activity)
            ref_dict = ref.as_dict() if hasattr(ref, "as_dict") else json.loads(json.dumps(ref, default=str))
            db.save_conversation_reference(ref_dict, conversation_type="channel")
            for member in turn_context.activity.members_added:
                if member.id != turn_context.activity.recipient.id:
                    await turn_context.send_activity(
                        Activity(type="message",
                                 text="Clambake bot connected. Type `/help` for commands."))

    async def _handle_command(self, cmd, arg):
        try:
            if cmd == "/status":
                return self._cmd_status()
            elif cmd == "/tasks":
                return self._cmd_tasks(arg)
            elif cmd == "/digest":
                return self._cmd_digest(arg)
            elif cmd == "/recall":
                return self._cmd_recall(arg)
            elif cmd == "/send":
                return self._cmd_send(arg)
            elif cmd == "/help":
                return self._help_text()
            else:
                return "Unknown command: %s\n\n%s" % (cmd, self._help_text())
        except Exception as e:
            return "Error: %s" % str(e)

    def _help_text(self):
        return (
            "**Clambake Bot Commands**\n\n"
            "- `/status` — Active agent instances\n"
            "- `/tasks [project]` — List active tasks\n"
            "- `/digest [hours]` — Activity summary\n"
            "- `/recall <query>` — Search memory\n"
            "- `/send <target> <message>` — Message an agent or @all\n"
            "- `/help` — This message"
        )

    def _cmd_status(self):
        instances = db.get_active_instances()
        if not instances:
            return "No active instances."
        lines = ["**Active Instances**\n"]
        for i in instances:
            task = i["current_task"] or "idle"
            age = i["seconds_since_heartbeat"]
            stale = " ⚠️" if age > 300 else ""
            lines.append("- [%s] **%s** — %s (%ds ago)%s" % (
                i["status"], i["project"], task, age, stale))
        return "\n".join(lines)

    def _cmd_tasks(self, arg):
        project = arg.strip() or None
        tasks = db.get_tasks(project=project)
        if not tasks:
            return "No tasks found."
        lines = ["**Tasks%s**\n" % (" — " + project if project else "")]
        for t in tasks:
            role = t["assigned_role"] or "any"
            inst = t["assigned_instance"][:8] if t["assigned_instance"] else "-"
            lines.append("- #%d [%s] %s (%s) → %s — %s" % (
                t["id"], t["status"], t["project"], role, inst, t["title"]))
        return "\n".join(lines)

    def _cmd_digest(self, arg):
        hours = 24
        if arg.strip().isdigit():
            hours = int(arg.strip())
        data = db.get_digest(hours)
        lines = ["**Digest (last %dh)**\n" % hours]

        # Instances
        if data["instances"]:
            lines.append("**Instances:** %d active" % len(data["instances"]))
        else:
            lines.append("**Instances:** none")

        # Tasks
        tc = data["task_counts"]
        if tc:
            parts = ["%s: %d" % (s, c) for s, c in tc.items()]
            lines.append("**Tasks:** %s" % ", ".join(parts))

        # Blockers
        if data["blockers"]:
            lines.append("\n🚨 **Active Blockers:**")
            for b in data["blockers"]:
                lines.append("- #%d %s — %s" % (b["id"], b["from_project"] or "?", b["subject"]))

        # Done tasks
        if data["done_tasks"]:
            lines.append("\n**Recently Completed:**")
            for t in data["done_tasks"]:
                lines.append("- #%d %s — %s" % (t["id"], t["project"], t["title"]))

        return "\n".join(lines)

    def _cmd_recall(self, arg):
        query = arg.strip()
        if not query:
            return "Usage: `/recall <search query>`"
        results = db.search_memory(query)
        if not results:
            return "No results for: %s" % query
        lines = ["**Memory Search: %s**\n" % query]
        for r in results:
            scope = "%s/%s" % (r["scope"], r.get("project") or "global")
            lines.append("- #%d [%s] **%s** — %s" % (
                r["id"], scope, r["title"], r["preview"]))
        return "\n".join(lines)

    def _cmd_send(self, arg):
        parts = arg.strip().split(None, 1)
        if len(parts) < 2:
            return "Usage: `/send <target> <message>`"
        target, message = parts
        msg_id = db.send_message(
            from_instance="teams-bot",
            from_project=None,
            to_target=target,
            message_type="info",
            subject=message,
        )
        return "Sent message #%d to %s" % (msg_id, target)
