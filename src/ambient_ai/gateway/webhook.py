"""POST /webhook/sms: the Twilio inbound message webhook.

Answers Twilio with an empty TwiML 200 at once; anything slower runs in a background task.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, Response

from ambient_ai.gateway.handlers import handle_mention, sms_reply
from ambient_ai.settings import MENTION, SMS_REPLY_MAX_CHARS
from ambient_ai.telemetry import log, redact

router = APIRouter()

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _empty_ok() -> Response:
    return Response(content=EMPTY_TWIML, media_type="application/xml", status_code=200)


@router.post("/webhook/sms")
async def inbound_sms(
    background: BackgroundTasks,
    body: Annotated[str, Form(alias="Body")] = "",
    sender: Annotated[str, Form(alias="From")] = "",
    to: Annotated[str, Form(alias="To")] = "",
) -> Response:
    if MENTION not in body:
        log.emit(
            "webhook.ignored", transport="sms",
            sender=redact(sender), to=redact(to), reason="no mention"
        )
        return _empty_ok()
    log.emit(
        "webhook.mention", transport="sms", sender=redact(sender), to=redact(to), chars=len(body)
    )
    background.add_task(_run_handler, sender, body)
    return _empty_ok()


def _run_handler(sender: str, body: str) -> None:
    try:
        handle_mention(sender, body, sms_reply(sender), max_reply_chars=SMS_REPLY_MAX_CHARS)
    except Exception as exc:  # noqa: BLE001 - the task must never raise into the server
        log.emit("handler.failed", sender=redact(sender), error=type(exc).__name__)
