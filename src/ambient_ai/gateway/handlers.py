"""What happens after the webhook has already answered the transport.

handle_mention(sender, body, reply) is the one seam: a connected sender's message goes
through run_mention and the reply text goes out through `reply`, a callback the transport
adapter built (SMS closes over send_sms; Telegram over sendMessage to the chat). Nothing
past this line knows which transport the message came from.

Credential management is settled here, before anything else, because it is the one branch
that must never reach a model: `private` says whether the conversation is one-to-one, and a
credential intent in a group is refused with nothing stored, logged, or extracted.

Every integration is optional, so a known sender is answered by the plan whether or not they
have connected anything. The connect link appears in exactly one place: the plan asked for
GitHub and this sender has no token, which run_mention answers with GITHUB_NOT_CONNECTED_REPLY
and this module turns into the link — delivered privately, because the link is one person's.

Extraction is scheduled only for a reply a model wrote. A fallback from run_mention says
GitHub, memory or the model was unreachable; a transcript ending in one holds no fact, so
`is_fallback_reply` keeps it out of memory rather than letting the extractor invent one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from ambient_ai.gateway.sms import send_sms
from ambient_ai.identity import lookup_sender, upsert_sender
from ambient_ai.identity.magic_link import portal_link
from ambient_ai.memory import schedule_extraction, transcript_of
from ambient_ai.orchestration.run import (
    GITHUB_NOT_CONNECTED_REPLY,
    is_fallback_reply,
    run_mention,
)
from ambient_ai.settings import SMS_REPLY_MAX_CHARS
from ambient_ai.telemetry import log, redact
from ambient_ai.tools import credentials

Reply = Callable[[str], None]
Forget = Callable[[], bool]

ONBOARD_TEXT = "I don't have a workspace for you yet! Tap here to authenticate: {link}"
CONNECT_TEXT = (
    "I need your permission to access GitHub to check that repo. "
    "Connect it — and anything else you want me to reach — here: {link}"
)


def sms_reply(to: str) -> Reply:
    """The SMS adapter's callback: every reply is a text to the sender's number."""

    def reply(body: str) -> None:
        send_sms(to, body)

    return reply


def handle_mention(
    sender: str,
    body: str,
    reply: Reply,
    *,
    link_reply: Reply | None = None,
    private: bool = True,
    forget_message: Forget | None = None,
    max_reply_chars: int = SMS_REPLY_MAX_CHARS,
) -> None:
    """`link_reply` carries the portal link when it must travel privately; defaults to `reply`.

    `private` is True when the conversation is one-to-one: SMS always is, a Telegram private
    chat is, a Telegram group is not. `forget_message` deletes the sender's own message on
    transports that can, and reports whether it worked, so a pasted token does not stay on
    their screen. `max_reply_chars` is the transport's reply limit: SMS is billed in
    160-character segments, Telegram is not, so each webhook passes its own.
    """
    deliver_link = link_reply or reply
    intent = credentials.parse_intent(body)
    if intent.action != "none":
        _handle_credentials(sender, intent, reply, private=private, forget_message=forget_message)
        return

    safe_body = credentials.scrub(body)
    profile = lookup_sender(sender)
    if profile is None:
        upsert_sender(sender)
        log.emit("identity.onboarding", sender=redact(sender))
        deliver_link(ONBOARD_TEXT.format(link=portal_link(sender)))
        return
    managed = False

    async def credentials_fn(action: str, tool: str) -> str:
        """The plan routed a credential action here; the store is the gateway's, not the model's."""
        nonlocal managed
        managed = True
        return await _apply(sender, credentials.CredentialIntent(action=action, tool=tool))

    answer = asyncio.run(
        run_mention(
            profile,
            safe_body,
            private=private,
            credentials_fn=credentials_fn,
            max_reply_chars=max_reply_chars,
        )
    )
    if answer == GITHUB_NOT_CONNECTED_REPLY:
        log.emit("identity.connect", sender=redact(sender), verified=profile.verified)
        deliver_link(CONNECT_TEXT.format(link=portal_link(sender)))
        return
    reply(answer)
    if not managed and not is_fallback_reply(answer):
        schedule_extraction(profile, transcript_of(safe_body, answer))


def _handle_credentials(
    sender: str,
    intent: credentials.CredentialIntent,
    reply: Reply,
    *,
    private: bool,
    forget_message: Forget | None,
) -> None:
    if not private:
        log.emit(
            "tools.managed",
            sender=redact(sender),
            action="refused",
            tool=intent.tool,
            reason="group chat",
        )
        reply(credentials.GROUP_REFUSAL)
        return
    upsert_sender(sender)
    text = asyncio.run(_apply(sender, intent))
    if intent.token is not None and forget_message is not None and not forget_message():
        text += credentials.DELETE_FAILED_NOTE
    reply(text)


async def _apply(sender: str, intent: credentials.CredentialIntent) -> str:
    text, outcome = await credentials.handle(sender, intent)
    log.emit(
        "tools.managed",
        sender=redact(sender),
        action=intent.action,
        tool=intent.tool,
        outcome=outcome,
    )
    return text
