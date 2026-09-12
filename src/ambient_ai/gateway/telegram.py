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


SHARE_NUMBER_BUTTON = "Share my number"
CONTACT_KEYBOARD: dict[str, Any] = {
    "keyboard": [[{"text": SHARE_NUMBER_BUTTON, "request_contact": True}]],
    "one_time_keyboard": True,
    "resize_keyboard": True,
}
"""One-tap `request_contact` button. Telegram only gives a webhook a user id, and the number
is the primary key on every transport (ADR-003), so this button is the whole identity bridge
on this transport. Reply keyboards are private-chat only: Telegram shows nothing in a group."""

REMOVE_KEYBOARD: dict[str, Any] = {"remove_keyboard": True}


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


def send_telegram(chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None) -> int:
    """Send plain text to a chat (a group, or a user's private chat). Returns the message id.

    `reply_markup` carries CONTACT_KEYBOARD or REMOVE_KEYBOARD; it is only ever passed for a
    private chat, because Telegram ignores a reply keyboard everywhere else.
    """
    text = text[:TELEGRAM_TEXT_MAX_CHARS]
    outbox = env("FAKE_TELEGRAM_OUTBOX")
    if outbox:
        with open(outbox, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"chat_id": chat_id, "text": text, "reply_markup": reply_markup}) + "\n"
            )
        log.emit("telegram.faked", to=redact(str(chat_id)), chars=len(text))
        return 0
    extra = {"reply_markup": reply_markup} if reply_markup else {}
    result = _api("sendMessage", chat_id=chat_id, text=text, **extra)
    log.emit("telegram.sent", to=redact(str(chat_id)), chars=len(text))
    return int(result["message_id"])


def delete_message(chat_id: int, message_id: int) -> bool:
    """Delete a message the sender sent us. True when Telegram deleted it.

    A bot may delete an incoming message in a private chat, which is how a pasted token stops
    being visible on the sender's own screen. A refusal is returned rather than raised: the
    sender has to be told the secret is still there so they can delete it themselves.
    """
    outbox = env("FAKE_TELEGRAM_OUTBOX")
    if outbox:
        with open(outbox, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"deleted": message_id, "chat_id": chat_id}) + "\n")
        return True
    try:
        _api("deleteMessage", chat_id=chat_id, message_id=message_id)
    except TelegramRefused as exc:
        log.emit("telegram.delete_refused", to=redact(str(chat_id)), error=str(exc)[:60])
        return False
    log.emit("telegram.deleted", to=redact(str(chat_id)))
    return True


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
