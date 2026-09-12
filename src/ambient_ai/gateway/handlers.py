"""What happens after the webhook has already answered the transport.

handle_mention(sender, body, reply) is the one seam: a connected sender's message goes
through run_mention and the reply text goes out through `reply`, a callback the transport
adapter built (SMS closes over send_sms; Telegram over sendMessage to the chat). Nothing
past this line knows which transport the message came from.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from ambient_ai.gateway.sms import send_sms
from ambient_ai.identity import lookup_sender, upsert_sender
from ambient_ai.identity.magic_link import portal_link
from ambient_ai.memory import schedule_extraction, transcript_of
from ambient_ai.orchestration.run import run_mention
from ambient_ai.telemetry import log, redact

Reply = Callable[[str], None]

ONBOARD_TEXT = "I don't have a workspace for you yet! Tap here to authenticate: {link}"
CONNECT_TEXT = (
    "I need your permission to access GitHub to check that repo. "
    "Secure your workspace and connect tools here: {link}"
)


def sms_reply(to: str) -> Reply:
    """The SMS adapter's callback: every reply is a text to the sender's number."""

    def reply(body: str) -> None:
        send_sms(to, body)

    return reply


def handle_mention(
    sender: str, body: str, reply: Reply, *, link_reply: Reply | None = None
) -> None:
    """`link_reply` carries the portal link when it must travel privately; defaults to `reply`."""
    deliver_link = link_reply or reply
    profile = lookup_sender(sender)
    if profile is None:
        upsert_sender(sender)
        log.emit("identity.onboarding", sender=redact(sender))
        deliver_link(ONBOARD_TEXT.format(link=portal_link(sender)))
        return
    if profile.github_token is None:
        log.emit("identity.connect", sender=redact(sender), verified=profile.verified)
        deliver_link(CONNECT_TEXT.format(link=portal_link(sender)))
        return
    answer = asyncio.run(run_mention(profile, body))
    reply(answer)
    schedule_extraction(profile, transcript_of(body, answer))
