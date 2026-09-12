"""POST /webhook/sms: the Twilio inbound message webhook."""

from typing import Annotated

from fastapi import APIRouter, Form, Response

from ambient_ai.settings import MENTION
from ambient_ai.telemetry import log, redact

router = APIRouter()

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/webhook/sms")
async def inbound_sms(
    body: Annotated[str, Form(alias="Body")] = "",
    sender: Annotated[str, Form(alias="From")] = "",
    to: Annotated[str, Form(alias="To")] = "",
) -> Response:
    if MENTION not in body:
        log.emit("webhook.ignored", sender=redact(sender), to=redact(to), reason="no mention")
        return Response(content=EMPTY_TWIML, media_type="application/xml", status_code=200)
    raise NotImplementedError("mention handling is not built yet")
