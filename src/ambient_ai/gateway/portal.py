"""The portal: GET /portal/{token} proves the number; POST stores the pasted GitHub token."""

from datetime import UTC, datetime
from html import escape
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

from ambient_ai.identity import set_token, upsert_sender
from ambient_ai.identity.magic_link import verify
from ambient_ai.telemetry import log, redact

router = APIRouter()

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Ambient AI</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem}}
input{{width:100%;padding:.6rem;font-size:1rem}}
button{{padding:.6rem 1.2rem;font-size:1rem;margin-top:.8rem}}
</style></head><body><h1>Ambient AI</h1>{content}</body></html>"""

FORM = """<p>Your number ending in <strong>{last4}</strong> is verified.</p>
<p>Paste a GitHub personal access token so the agent can read your repositories as you.</p>
<form method="post"><label for="github_token">GitHub token</label>
<input id="github_token" name="github_token" type="password" autocomplete="off" required>
<button type="submit">Connect GitHub</button></form>"""

DONE = """<p>GitHub is connected for the number ending in <strong>{last4}</strong>.</p>
<p>Go back to your thread and mention <code>@agent</code> again.</p>"""


def _phone_or_404(token: str) -> str:
    phone = verify(token)
    if phone is None:
        log.emit("portal.rejected")
        raise HTTPException(status_code=404)
    return phone


@router.get("/portal/{token}", response_class=HTMLResponse)
async def open_portal(token: str) -> str:
    phone = _phone_or_404(token)
    upsert_sender(phone, verified_at=datetime.now(UTC))
    log.emit("identity.verified", sender=redact(phone))
    return PAGE.format(content=FORM.format(last4=escape(redact(phone)[-4:])))


@router.post("/portal/{token}", response_class=HTMLResponse)
async def connect_github(token: str, github_token: Annotated[str, Form()]) -> str:
    phone = _phone_or_404(token)
    upsert_sender(phone, verified_at=datetime.now(UTC))
    set_token(phone, github_token.strip())
    log.emit("tools.connected", sender=redact(phone), tool="github")
    return PAGE.format(content=DONE.format(last4=escape(redact(phone)[-4:])))
