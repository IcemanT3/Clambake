"""Clambake Teams Bot — aiohttp entry point."""
import asyncio
import logging
import sys

from aiohttp import web
from microsoft_agents.botbuilder.core import (
    CloudAdapter,
    ConfigurationBotFrameworkAuthentication,
)

from bot import ClambakeBot
from notifications import NotificationPoller
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("clambake.teams")


# --- Auth + Adapter setup ---

class BotConfig:
    """Minimal config object for BotFrameworkAuthentication."""
    def __getattr__(self, key):
        config = {
            "MicrosoftAppId": Config.APP_ID,
            "MicrosoftAppPassword": Config.APP_PASSWORD,
            "MicrosoftAppType": "SingleTenant",
            "MicrosoftAppTenantId": "",
        }
        return config.get(key, "")


try:
    AUTH = ConfigurationBotFrameworkAuthentication(BotConfig())
    ADAPTER = CloudAdapter(AUTH)
except Exception:
    # Graceful degradation — SDK may not be available
    logger.warning("Bot Framework SDK not fully configured. Bot will start but may not authenticate.")
    ADAPTER = None

BOT = ClambakeBot()


# --- Routes ---

async def messages(request: web.Request) -> web.Response:
    """Main Bot Framework messaging endpoint."""
    if ADAPTER is None:
        return web.Response(status=503, text="Bot adapter not configured")

    body = await request.json()
    auth_header = request.headers.get("Authorization", "")

    response = await ADAPTER.process_activity(auth_header, body, BOT.on_turn)
    if response:
        return web.Response(status=response.status, body=response.body,
                            content_type="application/json")
    return web.Response(status=200)


async def health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({"status": "ok", "service": "clambake-teams-bot"})


# --- Startup / Shutdown ---

async def on_startup(app: web.Application):
    """Start the notification poller."""
    if ADAPTER and Config.APP_ID:
        poller = NotificationPoller(ADAPTER, Config.APP_ID)
        app["poller"] = poller
        app["poller_task"] = asyncio.create_task(poller.start())
        logger.info("Notification poller started")
    else:
        logger.warning("No APP_ID configured — notification poller disabled")


async def on_shutdown(app: web.Application):
    """Stop the notification poller."""
    poller = app.get("poller")
    if poller:
        poller.stop()
    task = app.get("poller_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    app = create_app()
    logger.info("Starting Clambake Teams Bot on port %d", Config.PORT)
    web.run_app(app, host="0.0.0.0", port=Config.PORT)
