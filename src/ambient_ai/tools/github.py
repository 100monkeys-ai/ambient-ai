"""GitHub as an integration: the sender's own token, and the check that it is live.

The check lives next to the token because a token stored unchecked only shows up as a failed
answer several messages later, by which time nobody suspects the token. Nothing here knows
about the registry; the registry names this module, so a second integration is added without
editing it.
"""

from __future__ import annotations

import httpx

from ambient_ai.identity.senders import credential_for
from ambient_ai.tools.github_rest import github_client

GITHUB = "github"

TRANSPORT: httpx.AsyncBaseTransport | None = None
"""Test seam: tests set this to an `httpx.MockTransport` so no check reaches GitHub."""


def github_token_for(phone: str) -> str | None:
    """Return the token the sender pasted in the portal, or None if GitHub is not connected."""
    credential = credential_for(phone, GITHUB)
    return credential.token if credential else None


async def validate_github_token(
    token: str, transport: httpx.AsyncBaseTransport | None = None
) -> str | None:
    """Return the login the token belongs to, or None when GitHub will not accept it."""
    async with github_client(token, transport or TRANSPORT) as client:
        try:
            response = await client.get("/user")
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        login = response.json().get("login")
    return str(login) if login else None
