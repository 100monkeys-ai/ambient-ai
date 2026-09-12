"""POST /webhook/telegram: the Bot API update webhook, the second transport.

Answers Telegram with 200 at once; the mention goes to a background task that calls the
same handle_mention as SMS. The sender key is `tg:<user_id>`; the reply goes back to the
chat the message came from; a portal link goes to the user privately when Telegram allows.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response

from ambient_ai.gateway.handlers import handle_mention
from ambient_ai.gateway.telegram import TelegramRefused, get_me, send_telegram, set_webhook
from ambient_ai.settings import MENTION, env, telegram_webhook_secret
from ambient_ai.telemetry import log, redact

router = APIRouter()

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
GROUP_FALLBACK_NOTE = (
    "I couldn't message you privately (start a chat with me first next time), "
    "so here is your link: "
)

_bot: dict[str, Any] = {"username": None, "id": None}
"""The bot's own identity from getMe, cached at startup; mention detection reads it."""


def set_bot_identity(*, username: str | None, bot_id: int | None) -> None:
    _bot["username"] = username.lower() if username else None
    _bot["id"] = bot_id


def register_bot() -> None:
    """Startup: when TELEGRAM_BOT_TOKEN is set, cache getMe and set the webhook. Never raises."""
    if not env("TELEGRAM_BOT_TOKEN"):
        log.emit("telegram.startup", status="skipped", reason="no token")
        return
    try:
        me = get_me()
        set_bot_identity(username=me.get("username"), bot_id=me.get("id"))
        set_webhook()
    except Exception as exc:  # noqa: BLE001 - the gateway must start without Telegram
        log.emit("telegram.startup", status="failed", error=type(exc).__name__)
        return
    log.emit("telegram.startup", status="ok", username=_bot["username"])


def _mentioned_usernames(text: str, entities: list[dict[str, Any]]) -> set[str]:
    names = {
        text[e["offset"] + 1 : e["offset"] + e["length"]].lower()
        for e in entities
        if e.get("type") == "mention"
    }
    names |= {word[1:].lower() for word in text.split() if word.startswith("@")}
    return names


def is_addressed(message: dict[str, Any]) -> bool:
    """Private chat: always. Group: `@<bot_username>`, `@agent`, or a reply to the bot."""
    if message.get("chat", {}).get("type") == "private":
        return True
    text = message.get("text") or ""
    names = _mentioned_usernames(text, message.get("entities") or [])
    if MENTION[1:].lower() in names or (_bot["username"] and _bot["username"] in names):
        return True
    replied = message.get("reply_to_message", {}).get("from", {})
    return _bot["id"] is not None and replied.get("id") == _bot["id"]


@router.post("/webhook/telegram")
async def inbound_telegram(
    request: Request,
    background: BackgroundTasks,
    secret: str | None = Header(default=None, alias=SECRET_HEADER),
) -> Response:
    if not secret or not hmac.compare_digest(secret, telegram_webhook_secret()):
        log.emit("webhook.rejected", transport="telegram", reason="bad secret")
        raise HTTPException(status_code=403)
    update = await request.json()
    message = update.get("message") or {}
    text = message.get("text")
    user_id = message.get("from", {}).get("id")
    chat_id = message.get("chat", {}).get("id")
    if not text or user_id is None or chat_id is None:
        log.emit("webhook.ignored", transport="telegram", reason="no text message")
        return Response(status_code=200)
    who, chat = redact(str(user_id)), redact(str(chat_id))
    if not is_addressed(message):
        log.emit("webhook.ignored", transport="telegram", sender=who, to=chat, reason="no mention")
        return Response(status_code=200)
    log.emit("webhook.mention", transport="telegram", sender=who, to=chat, chars=len(text))
    background.add_task(_run_handler, user_id, chat_id, text)
    return Response(status_code=200)


def _run_handler(user_id: int, chat_id: int, text: str) -> None:
    sender = f"tg:{user_id}"

    def reply(body: str) -> None:
        send_telegram(chat_id, body)

    def link_reply(body: str) -> None:
        try:
            send_telegram(user_id, body)
        except TelegramRefused as exc:
            log.emit("telegram.private_refused", sender=redact(sender), error=str(exc)[:60])
            send_telegram(chat_id, GROUP_FALLBACK_NOTE + body)

    try:
        handle_mention(sender, text, reply, link_reply=link_reply)
    except Exception as exc:  # noqa: BLE001 - the task must never raise into the server
        log.emit("handler.failed", sender=redact(sender), error=type(exc).__name__)
