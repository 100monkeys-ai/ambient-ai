"""Process configuration. Reads names from the environment; never logs values."""

import os
import secrets

from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER = "anthropic"

LLM_MODEL_ID = "claude-opus-5"
"""The model behind the orchestrator, the Execution Agent, and synthesis."""

EXTRACTOR_MODEL_ID = "claude-haiku-4-5"
"""The model behind the memory extractor: a bulk job off the reply path, so a cheaper one."""

MENTION = "@agent"
"""The token that wakes the agent. Messages without it are dropped at the gateway."""

MAGIC_LINK_TTL_SECONDS = 15 * 60
"""How long a texted magic link opens the portal."""

SMS_REPLY_MAX_CHARS = 480
"""Hard cap on a synthesised reply: three SMS segments, plain text."""

OUTBOUND_TIMEOUT_SECONDS = 10.0
"""Every Cortex MCP and GitHub REST call on the request path times out here."""

ENV_NAMES = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "APP_SECRET",
    "PORTAL_BASE_URL",
    "DB_PATH",
    "ANTHROPIC_API_KEY",
    "CORTEX_MCP_URL",
    "CORTEX_MCP_TOKEN",
    "CORTEX_WORKSPACE",
    "TELEMETRY_STREAM",
    "TELEMETRY_LOG_FILE",
    "FAKE_SMS_OUTBOX",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "FAKE_TELEGRAM_OUTBOX",
)


def env(name: str) -> str | None:
    """Return an environment variable by name, or None when unset."""
    return os.environ.get(name)


def require(name: str) -> str:
    """Return an environment variable or raise naming the missing variable, never a value."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; add it to .env")
    return value


def db_path() -> str:
    """SQLite file holding sender records. Git-ignored; defaults to ./ambient.db."""
    return env("DB_PATH") or "./ambient.db"


def portal_base_url() -> str:
    """Where magic links point. The public tunnel host in a demo; localhost otherwise."""
    return (env("PORTAL_BASE_URL") or "http://localhost:8000").rstrip("/")


_generated_webhook_secret = secrets.token_urlsafe(32)


def telegram_webhook_secret() -> str:
    """TELEGRAM_WEBHOOK_SECRET, or one generated per process; setWebhook registers it at startup."""
    return env("TELEGRAM_WEBHOOK_SECRET") or _generated_webhook_secret
