"""POST /webhook/telegram: the Bot API update webhook, the second transport.

Answers Telegram with 200 at once; the mention goes to a background task that calls the
same handle_mention as SMS. The sender key is the sender's E.164 number (ADR-003); Telegram
gives the webhook only a user id, so the bot asks each new sender to share their contact and
maps the id onto the number. Until they do, the key is a provisional `tg:<user_id>`. The
reply goes back to the chat the message came from; anything carrying a link or the contact
keyboard goes to the user privately, falling back to the group when Telegram refuses.
"""

from __future__ import annotations

import asyncio
import hmac
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response

from ambient_ai.gateway.handlers import handle_mention
from ambient_ai.gateway.telegram import (
    CONTACT_KEYBOARD,
    REMOVE_KEYBOARD,
    TelegramRefused,
    delete_message,
    get_me,
    send_telegram,
    set_webhook,
)
from ambient_ai.identity import (
    adopt_telegram_contact,
    is_provisional,
    resolve_telegram_sender,
)
from ambient_ai.memory.writer import migrate_memory_page
from ambient_ai.settings import MENTION, env, telegram_webhook_secret
from ambient_ai.telemetry import log, redact

router = APIRouter()

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
TELEGRAM_REPLY_MAX_CHARS = 1500
"""Telegram has no 160-character segment and no per-segment charge, so a reply that
summarises a repository is not cut to an SMS. Well under the Bot API's 4096-character
message limit, and short enough to stay a chat message rather than a document."""
GROUP_FALLBACK_NOTE = (
    "I couldn't message you privately (start a chat with me first next time), "
    "so here is your link: "
)
SHARE_NUMBER_NOTE = (
    "\n\nYour phone number is your account here: it is how I keep your memory and your "
    "tools yours and nobody else's. Tap Share my number below."
)
CONTACT_REQUEST_TEXT = "Before I remember anything for you, I need to know who you are." + (
    SHARE_NUMBER_NOTE
)
NUMBER_LINKED_TEXT = "Thanks — you're {number} from now on. Everything I remember is yours."
CONTACT_NOT_YOURS_TEXT = (
    "That's somebody else's contact. Tap Share my number so I know it's really you."
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
    contact = message.get("contact")
    user_id = message.get("from", {}).get("id")
    chat_id = message.get("chat", {}).get("id")
    if user_id is None or chat_id is None:
        log.emit("webhook.ignored", transport="telegram", reason="no addressable message")
        return Response(status_code=200)
    who, chat = redact(str(user_id)), redact(str(chat_id))
    if contact is not None:
        log.emit("webhook.contact", transport="telegram", sender=who)
        background.add_task(_run_contact, user_id, chat_id, contact)
        return Response(status_code=200)
    if not text:
        log.emit("webhook.ignored", transport="telegram", sender=who, reason="no text message")
        return Response(status_code=200)
    if not is_addressed(message):
        log.emit("webhook.ignored", transport="telegram", sender=who, to=chat, reason="no mention")
        return Response(status_code=200)
    log.emit("webhook.mention", transport="telegram", sender=who, to=chat, chars=len(text))
    background.add_task(
        _run_handler,
        user_id,
        chat_id,
        text,
        message.get("message_id"),
        message.get("chat", {}).get("type") == "private",
        message.get("from", {}).get("first_name"),
    )
    return Response(status_code=200)


def _run_handler(
    user_id: int,
    chat_id: int,
    text: str,
    message_id: int | None,
    private: bool,
    first_name: str | None = None,
) -> None:
    sender = resolve_telegram_sender(user_id)
    needs_number = is_provisional(sender)
    asked = False

    def reply(body: str) -> None:
        send_telegram(chat_id, body)

    def link_reply(body: str) -> None:
        """Private, because it carries a link and possibly the keyboard; group is the fallback."""
        nonlocal asked
        try:
            send_telegram(
                user_id,
                body + (SHARE_NUMBER_NOTE if needs_number else ""),
                reply_markup=CONTACT_KEYBOARD if needs_number else None,
            )
            asked = needs_number
        except TelegramRefused as exc:
            log.emit("telegram.private_refused", sender=redact(sender), error=str(exc)[:60])
            send_telegram(chat_id, GROUP_FALLBACK_NOTE + body)

    def forget_message() -> bool:
        """Take the sender's own message off their screen once it has carried a secret."""
        return message_id is not None and delete_message(chat_id, message_id)

    try:
        handle_mention(
            sender,
            text,
            reply,
            link_reply=link_reply,
            private=private,
            first_name=first_name,
            forget_message=forget_message,
            max_reply_chars=TELEGRAM_REPLY_MAX_CHARS,
        )
        if needs_number and not asked:
            _ask_for_number(user_id, sender)
    except Exception as exc:  # noqa: BLE001 - the task must never raise into the server
        log.emit("handler.failed", sender=redact(sender), error=type(exc).__name__)


def _ask_for_number(user_id: int, sender: str) -> None:
    """A sender already holding a token still has no number; ask privately, never in a group."""
    try:
        send_telegram(user_id, CONTACT_REQUEST_TEXT, reply_markup=CONTACT_KEYBOARD)
    except TelegramRefused as exc:
        log.emit("telegram.private_refused", sender=redact(sender), error=str(exc)[:60])


def _run_contact(user_id: int, chat_id: int, contact: dict[str, Any]) -> None:
    """A shared contact is the identity bridge: only the sender's own number is accepted.

    Telegram lets a user forward anybody's contact card, so a card whose `user_id` is not the
    sender's own would let one person claim another's memory and tools.
    """
    if contact.get("user_id") != user_id:
        log.emit(
            "identity.contact_ignored", sender=redact(str(user_id)), reason="not their own contact"
        )
        send_telegram(chat_id, CONTACT_NOT_YOURS_TEXT, reply_markup=CONTACT_KEYBOARD)
        return
    try:
        profile, merged = adopt_telegram_contact(user_id, contact.get("phone_number") or "")
        log.emit("identity.number_linked", sender=redact(profile.phone), merged=merged is not None)
        if merged is not None:
            asyncio.run(migrate_memory_page(merged, profile.phone))
        send_telegram(
            chat_id,
            NUMBER_LINKED_TEXT.format(number=redact(profile.phone)),
            reply_markup=REMOVE_KEYBOARD,
        )
    except Exception as exc:  # noqa: BLE001 - the task must never raise into the server
        log.emit("handler.failed", sender=redact(str(user_id)), error=type(exc).__name__)
