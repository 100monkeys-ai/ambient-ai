"""Process configuration. Reads names from the environment; never logs values."""

import os

from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER = "anthropic"
LLM_MODEL_ID = "claude-sonnet-5"
"""The single model id used by every agent. Change it here, nowhere else."""

MENTION = "@agent"
"""The token that wakes the agent. Messages without it are dropped at the gateway."""

ENV_NAMES = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "TWILIO_VERIFY_SERVICE_SID",
    "ANTHROPIC_API_KEY",
    "CORTEX_MCP_URL",
    "CORTEX_MCP_TOKEN",
    "GITHUB_TOKEN",
)


def env(name: str) -> str | None:
    """Return an environment variable by name, or None when unset."""
    return os.environ.get(name)
