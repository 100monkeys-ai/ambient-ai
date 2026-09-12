"""Outbound SMS. One function, one transport: the Twilio Messages API.

Set FAKE_SMS_OUTBOX to a file path to record sends there instead of calling Twilio
(used for local curl runs). Tests replace send_sms with a double instead.
"""

from __future__ import annotations

import json

from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from ambient_ai.settings import env, require
from ambient_ai.telemetry import log, redact

SEND_TIMEOUT_SECONDS = 10


def send_sms(to: str, body: str) -> str:
    """Send one text from the demo number. Returns the message sid (or 'fake')."""
    outbox = env("FAKE_SMS_OUTBOX")
    if outbox:
        with open(outbox, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"to": to, "body": body}) + "\n")
        log.emit("sms.faked", to=redact(to), chars=len(body))
        return "fake"
    sender = require("TWILIO_PHONE_NUMBER")
    client = Client(
        require("TWILIO_ACCOUNT_SID"),
        require("TWILIO_AUTH_TOKEN"),
        http_client=TwilioHttpClient(timeout=SEND_TIMEOUT_SECONDS),
    )
    message = client.messages.create(to=to, from_=sender, body=body)
    log.emit("sms.sent", to=redact(to), chars=len(body), sid=message.sid)
    return message.sid
