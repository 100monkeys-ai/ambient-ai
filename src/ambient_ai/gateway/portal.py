"""The portal: GET /portal/{token} proves the number, then one card per integration.

The page is built from `tools.registry`, so an integration added there appears here with no
edit. Every integration is optional: the agent answers with none connected and gains one
ability per connection, and the page says so rather than demanding a key before anything
works.

A pasted key is checked against the service before it is stored, the same check the `/tools`
command runs, because a key stored unchecked only shows up as a failed answer several
messages later — by which time nobody suspects the key.
"""

from datetime import UTC, datetime
from html import escape
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

from ambient_ai.identity import (
    SenderProfile,
    clear_credential,
    set_credential,
    upsert_sender,
)
from ambient_ai.identity.magic_link import verify
from ambient_ai.telemetry import log, redact
from ambient_ai.tools import registry

router = APIRouter()

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Ambient AI</title>
<style>
:root{{color-scheme:light dark}}
body{{font-family:system-ui,-apple-system,sans-serif;max-width:34rem;margin:0 auto;
padding:2rem 16px;line-height:1.5}}
h1{{font-size:1.5rem;margin-bottom:.25rem}}
h2{{font-size:1.1rem;margin:0 0 .25rem}}
p{{margin:.4rem 0}}
.lede{{opacity:.8}}
.card{{border:1px solid;border-color:color-mix(in srgb, currentColor 30%, transparent);
border-radius:12px;padding:1rem;margin:1rem 0}}
.status{{font-weight:600}}
.notice{{border-left:4px solid;padding-left:.6rem}}
label{{display:block;margin-top:.6rem;font-size:.9rem;opacity:.8}}
input{{width:100%;box-sizing:border-box;padding:.6rem;font-size:1rem;
border-radius:8px;border:1px solid;
border-color:color-mix(in srgb, currentColor 40%, transparent);
background:transparent;color:inherit}}
button{{padding:.6rem 1.2rem;font-size:1rem;margin-top:.8rem;border-radius:8px;
border:1px solid;border-color:color-mix(in srgb, currentColor 40%, transparent);
background:transparent;color:inherit}}
a{{color:inherit}}
</style></head><body><h1>Ambient AI</h1>{content}</body></html>"""

HEADER = """<p class="lede">Your number ending in <strong>{last4}</strong> is verified.</p>
<p class="lede">Every integration below is optional. The agent already answers from your own
memory with nothing connected; each key you add gives it one more ability.</p>"""

CARD = """<div class="card"><h2>{name}</h2>
<p>{description}</p>
<p class="status">{status}</p>{notice}
<p><a href="{url}" target="_blank" rel="noreferrer">Where to get a key</a></p>
<form method="post" action="{action}">
<label for="key-{key}">{field_label}</label>
<input id="key-{key}" name="key" type="password" autocomplete="off" required>
<button type="submit" name="action" value="connect">{verb}</button>
</form>{disconnect}"""

DISCONNECT = """<form method="post" action="{action}">
<button type="submit" name="action" value="disconnect">Disconnect</button></form>"""

FOOTER = "<p>Go back to your thread and mention <code>@agent</code> again.</p>"


def _phone_or_404(token: str) -> str:
    phone = verify(token)
    if phone is None:
        log.emit("portal.rejected")
        raise HTTPException(status_code=404)
    return phone


def _card(
    integration: registry.Integration, profile: SenderProfile, token: str, notice: str
) -> str:
    credential = profile.credential(integration.key)
    if credential is None:
        status = "Not connected"
        verb = f"Connect {integration.name}"
        disconnect = ""
    else:
        when = credential.added_at.date().isoformat() if credential.added_at else "earlier"
        who = escape(credential.login) if credential.login else "an unnamed account"
        status = f"Connected as {who}, added {when}"
        verb = "Replace key"
        disconnect = DISCONNECT.format(action=f"/portal/{token}/{integration.key}")
    return CARD.format(
        name=escape(integration.name),
        description=escape(integration.description),
        status=status,
        notice=notice,
        url=escape(integration.how_to_get_a_key_url),
        action=f"/portal/{token}/{integration.key}",
        key=escape(integration.key),
        field_label=escape(integration.field_label),
        verb=escape(verb),
        disconnect=disconnect,
    )


def _page(phone: str, token: str, notices: dict[str, str] | None = None) -> str:
    profile = upsert_sender(phone, verified_at=datetime.now(UTC))
    notices = notices or {}
    cards = "".join(
        _card(integration, profile, token, notices.get(integration.key, ""))
        for integration in registry.integrations()
    )
    header = HEADER.format(last4=escape(redact(phone)[-4:]))
    return PAGE.format(content=header + cards + FOOTER)


@router.get("/portal/{token}", response_class=HTMLResponse)
async def open_portal(token: str) -> str:
    phone = _phone_or_404(token)
    page = _page(phone, token)
    log.emit("identity.verified", sender=redact(phone))
    return page


@router.post("/portal/{token}/{integration}", response_class=HTMLResponse)
async def manage_integration(
    token: str,
    integration: str,
    action: Annotated[str, Form()] = "connect",
    key: Annotated[str, Form()] = "",
) -> str:
    """One card, one form, one action. A rejected key stores nothing and says so on its card."""
    phone = _phone_or_404(token)
    entry = registry.find(integration)
    if entry is None:
        raise HTTPException(status_code=404)

    if action == "disconnect":
        clear_credential(phone, entry.key)
        log.emit("tools.disconnected", sender=redact(phone), tool=entry.key)
        return _page(phone, token, {entry.key: f'<p class="notice">{entry.name} disconnected.</p>'})

    pasted = key.strip()
    login = await entry.validate(pasted) if pasted else None
    if login is None:
        log.emit("tools.rejected", sender=redact(phone), tool=entry.key)
        notice = (
            f'<p class="notice">That key was rejected by {escape(entry.name)}; nothing was '
            "saved. Check it has not expired, then paste a current one.</p>"
        )
        return _page(phone, token, {entry.key: notice})

    upsert_sender(phone)
    set_credential(phone, entry.key, pasted, login=login)
    log.emit("tools.connected", sender=redact(phone), tool=entry.key)
    notice = f'<p class="notice">{escape(entry.name)} connected as {escape(login)}.</p>'
    return _page(phone, token, {entry.key: notice})
