"""Outbound Telegram: the Bot API over httpx. sendMessage, getMe, setWebhook.

Set FAKE_TELEGRAM_OUTBOX to a file path to record sends there instead of calling Telegram
(used for local curl runs). Tests replace send_telegram with a double instead.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ambient_ai.settings import env, portal_base_url, require, telegram_webhook_secret
from ambient_ai.telemetry import log, redact

SEND_TIMEOUT_SECONDS = 10.0
TELEGRAM_TEXT_MAX_CHARS = 4096
"""Bot API limit for one sendMessage; orchestration already clips to the 480-char SMS shape."""


class TelegramRefused(Exception):
    """Telegram answered with an error for this chat (e.g. the user never started the bot)."""


def _api(method: str, **params: Any) -> dict[str, Any]:
    token = require("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/{method}"
    response = httpx.post(url, json=params, timeout=SEND_TIMEOUT_SECONDS)
    payload = response.json() if response.content else {}
    if response.status_code >= 400 or not payload.get("ok"):
        raise TelegramRefused(payload.get("description") or f"HTTP {response.status_code}")
    return payload["result"]


def send_telegram(chat_id: int, text: str) -> int:
    """Send plain text to a chat (a group, or a user's private chat). Returns the message id."""
    text = text[:TELEGRAM_TEXT_MAX_CHARS]
    outbox = env("FAKE_TELEGRAM_OUTBOX")
    if outbox:
        with open(outbox, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"chat_id": chat_id, "text": text}) + "\n")
        log.emit("telegram.faked", to=redact(str(chat_id)), chars=len(text))
        return 0
    result = _api("sendMessage", chat_id=chat_id, text=text)
    log.emit("telegram.sent", to=redact(str(chat_id)), chars=len(text))
    return int(result["message_id"])


def get_me() -> dict[str, Any]:
    """The bot's own identity: at least `id` and `username`."""
    return _api("getMe")


def set_webhook() -> bool:
    """Point Telegram at PORTAL_BASE_URL/webhook/telegram with the secret header. Idempotent."""
    return bool(
        _api(
            "setWebhook",
            url=f"{portal_base_url()}/webhook/telegram",
            secret_token=telegram_webhook_secret(),
            allowed_updates=["message"],
        )
    )
