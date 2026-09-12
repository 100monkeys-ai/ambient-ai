"""What happens after the webhook has already answered Twilio.

handle_mention(sender, body) is the one seam: phase 3 (orchestration) replaces the
acknowledgement branch and nothing else.
"""

from __future__ import annotations

from ambient_ai.gateway.sms import send_sms
from ambient_ai.identity import lookup_sender, upsert_sender
from ambient_ai.identity.magic_link import portal_link
from ambient_ai.telemetry import log, redact

ONBOARD_TEXT = "I don't have a workspace for you yet! Tap here to authenticate: {link}"
CONNECT_TEXT = (
    "I need your permission to access GitHub to check that repo. "
    "Secure your workspace and connect tools here: {link}"
)
ACK_TEXT = "Got it. Working on: {excerpt}"


def handle_mention(sender: str, body: str) -> None:
    profile = lookup_sender(sender)
    if profile is None:
        upsert_sender(sender)
        log.emit("identity.onboarding", sender=redact(sender))
        send_sms(sender, ONBOARD_TEXT.format(link=portal_link(sender)))
        return
    if profile.github_token is None:
        log.emit("identity.connect", sender=redact(sender), verified=profile.verified)
        send_sms(sender, CONNECT_TEXT.format(link=portal_link(sender)))
        return
    log.emit("mention.acknowledged", sender=redact(sender), chars=len(body))
    send_sms(sender, ACK_TEXT.format(excerpt=body[:60]))
