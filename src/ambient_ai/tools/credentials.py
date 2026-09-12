"""Managing the sender's own tool credentials from the conversation: list, add, replace, remove.

Rule one is the kind of conversation. A GitHub token is one person's credential, so it is
only ever handled where the conversation is one-to-one: a Telegram private chat, or SMS. In a
group the intent is refused before anything is parsed, stored, logged, or extracted — a token
pasted into a group is already visible to everyone in the room, and echoing any part of it
back, or storing it because a room said so, makes that worse rather than better.

Rule two is that the secret stays on this path. `parse_intent` runs before any model call, so
a message carrying something token-shaped is handled deterministically and the model never
sees it; `scrub` masks anything token-shaped in every body that goes on to the plan, the
memory extractor, telemetry, or a reply. Replies never show more than the last four
characters, which is enough for a person to tell two of their own tokens apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import httpx

from ambient_ai.identity import clear_token, lookup_sender, set_token, upsert_sender
from ambient_ai.settings import MENTION
from ambient_ai.tools.github_rest import github_client

Action = Literal["list", "add", "remove", "none"]

GITHUB = "github"
COMMAND = "/tools"
ADD_WORDS = frozenset({"add", "replace", "set", "connect", "update"})
REMOVE_WORDS = frozenset({"remove", "delete", "disconnect", "revoke", "forget"})
LIST_WORDS = frozenset({"list", "show", "status", "ls"})

TOKEN_PATTERN = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{8,}"  # classic personal access, OAuth, server, user, refresh
    r"|github_pat_[A-Za-z0-9_]{8,}"  # fine-grained personal access token
    r"|\b[A-Za-z0-9]{36,}\b"  # any long unbroken base62 run: treat it as a secret
)
"""What counts as token-shaped. Deliberately wide: a false positive costs one refused
sentence, a false negative puts a live credential into a model prompt and a log line."""

MASKED = "[token]"


def find_token(text: str) -> str | None:
    match = TOKEN_PATTERN.search(text)
    return match.group(0) if match else None


def contains_token(text: str) -> bool:
    return TOKEN_PATTERN.search(text) is not None


def scrub(text: str) -> str:
    """Replace every token-shaped run with `[token]`. Run on any body leaving this module."""
    return TOKEN_PATTERN.sub(MASKED, text)


def mask(token: str) -> str:
    return "…" + token[-4:]


@dataclass(frozen=True)
class CredentialIntent:
    action: Action = "none"
    tool: str = GITHUB
    token: str | None = None


def parse_intent(body: str) -> CredentialIntent:
    """Read a credential intent off the raw message, deterministically and before any model.

    Two entry points: the explicit `/tools` command, and a bare token-shaped string, which is
    always an add — somebody pasting a token means to connect it, and asking a model first
    would be sending the secret to a third party to find that out.
    """
    text = body.replace(MENTION, " ").strip()
    token = find_token(text)
    words = text.split()
    if words and words[0].lower() == COMMAND:
        rest = [w for w in words[1:] if w != token]
        if not rest:
            return CredentialIntent(action="list")
        verb = rest[0].lower()
        tool = rest[1].lower() if len(rest) > 1 else GITHUB
        if verb in ADD_WORDS:
            return CredentialIntent(action="add", tool=tool, token=token)
        if verb in REMOVE_WORDS:
            return CredentialIntent(action="remove", tool=tool)
        if verb in LIST_WORDS:
            return CredentialIntent(action="list", tool=tool)
        return CredentialIntent(action="list")
    if token is not None:
        return CredentialIntent(action="add", tool=GITHUB, token=token)
    return CredentialIntent()


TRANSPORT: httpx.AsyncBaseTransport | None = None
"""Test seam: tests set this to an `httpx.MockTransport` so no check reaches GitHub."""


async def validate_github_token(
    token: str, transport: httpx.AsyncBaseTransport | None = None
) -> str | None:
    """Return the login the token belongs to, or None when GitHub will not accept it.

    A token is checked before it is stored, because a typo stored silently only shows up as a
    failed answer several messages later, by which time nobody suspects the token.
    """
    async with github_client(token, transport or TRANSPORT) as client:
        try:
            response = await client.get("/user")
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        login = response.json().get("login")
    return str(login) if login else None


GROUP_REFUSAL = "Manage your tools in a private chat with me."
NO_TOOLS = "No tools connected. Paste a GitHub token here or use the portal link."
ONLY_GITHUB = "I only manage GitHub tokens right now."
ASK_FOR_TOKEN = "Paste your GitHub personal access token here and I'll connect it."
REJECTED = "GitHub rejected that token, so I stored nothing. Paste a current one and I'll retry."
DISCONNECTED = "GitHub disconnected."
NOT_CONNECTED = "GitHub wasn't connected, so there was nothing to remove."
DELETE_FAILED_NOTE = (
    " I couldn't delete your message — delete it yourself so the token isn't left in the chat."
)


async def handle(
    phone: str, intent: CredentialIntent, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, str]:
    """Apply one credential intent for one sender. Returns (reply text, telemetry outcome).

    The caller has already established that the conversation is private; this function never
    checks it again and never puts more than four characters of a token in what it returns.
    """
    if intent.tool != GITHUB:
        return ONLY_GITHUB, "unknown tool"
    profile = lookup_sender(phone)
    stored = profile.github_token if profile else None

    if intent.action == "list":
        if not stored:
            return NO_TOOLS, "none"
        when = profile.token_added_at.date().isoformat() if profile.token_added_at else "earlier"
        who = f" as {profile.github_login}" if profile.github_login else ""
        return f"GitHub: connected{who} ({mask(stored)}), added {when}.", "listed"

    if intent.action == "add":
        if not intent.token:
            return ASK_FOR_TOKEN, "no token"
        login = await validate_github_token(intent.token, transport)
        if login is None:
            return REJECTED, "rejected"
        upsert_sender(phone)
        set_token(phone, intent.token, login=login)
        return f"GitHub connected as {login} ({mask(intent.token)}).", "stored"

    if intent.action == "remove":
        if not stored:
            return NOT_CONNECTED, "none"
        clear_token(phone)
        return DISCONNECTED, "cleared"

    return NO_TOOLS, "none"
