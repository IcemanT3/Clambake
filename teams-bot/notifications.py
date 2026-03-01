"""Background poller for proactive Teams notifications."""
import asyncio
import json
import logging

from microsoft_agents.botbuilder.core import TurnContext
from microsoft_agents.connector.models import (
    Activity,
    ConversationReference,
)

import db
from config import Config

logger = logging.getLogger("clambake.notifications")


class NotificationPoller:
    """Polls DB for events and sends proactive Teams messages."""

    def __init__(self, adapter, bot_id):
        self.adapter = adapter
        self.bot_id = bot_id
        self._running = False

    async def start(self):
        self._running = True
        logger.info("Notification poller started (interval=%ds, lookback=%dm)",
                     Config.POLL_INTERVAL, Config.LOOKBACK_MINUTES)
        while self._running:
            try:
                await self._poll()
            except Exception:
                logger.exception("Poller error")
            await asyncio.sleep(Config.POLL_INTERVAL)

    def stop(self):
        self._running = False

    async def _poll(self):
        events = db.get_new_events(Config.LOOKBACK_MINUTES)
        for event in events:
            if db.is_already_notified(event["type"], event["source_id"]):
                continue

            sent = False
            if "channel" in event["targets"]:
                sent |= await self._send_to_channels(event["text"])
            if "dm" in event["targets"]:
                sent |= await self._send_to_dms(event["text"])

            if sent:
                db.mark_notified(event["type"], event["source_id"])
                logger.info("Notified: [%s] %s", event["type"], event["source_id"])

    async def _send_to_channels(self, text):
        refs = db.get_channel_references()
        if not refs:
            return False
        sent = False
        for ref_row in refs:
            try:
                ref_dict = ref_row["reference_json"]
                if isinstance(ref_dict, str):
                    ref_dict = json.loads(ref_dict)
                conv_ref = ConversationReference.from_dict(ref_dict)
                await self._send_proactive(conv_ref, text)
                sent = True
            except Exception:
                logger.exception("Failed to send to channel %s", ref_row.get("conversation_id"))
        return sent

    async def _send_to_dms(self, text):
        refs = db.get_personal_references()
        if not refs:
            return False
        sent = False
        for ref_row in refs:
            try:
                ref_dict = ref_row["reference_json"]
                if isinstance(ref_dict, str):
                    ref_dict = json.loads(ref_dict)
                conv_ref = ConversationReference.from_dict(ref_dict)
                await self._send_proactive(conv_ref, text)
                sent = True
            except Exception:
                logger.exception("Failed to DM user %s", ref_row.get("user_name"))
        return sent

    async def _send_proactive(self, conv_ref, text):
        """Send a proactive message using the stored conversation reference."""
        async def callback(turn_context: TurnContext):
            await turn_context.send_activity(Activity(type="message", text=text))

        await self.adapter.continue_conversation(
            conv_ref, callback, self.bot_id
        )
