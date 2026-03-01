"""Configuration from environment variables."""
import os


class Config:
    # Azure Bot Framework
    APP_ID = os.environ.get("TEAMS_BOT_APP_ID", "")
    APP_PASSWORD = os.environ.get("TEAMS_BOT_APP_PASSWORD", "")

    # Database
    DB_HOST = os.environ.get("CLAMBAKE_DB_HOST", "localhost")
    DB_PORT = os.environ.get("CLAMBAKE_DB_PORT", "5433")
    DB_NAME = os.environ.get("CLAMBAKE_DB_NAME", "docdb")
    DB_USER = os.environ.get("CLAMBAKE_DB_USER", "postgres")
    DB_PASS = os.environ.get("CLAMBAKE_DB_PASS", "postgres")

    # Poller
    POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
    LOOKBACK_MINUTES = int(os.environ.get("LOOKBACK_MINUTES", "5"))

    # Server
    PORT = int(os.environ.get("PORT", "3978"))
