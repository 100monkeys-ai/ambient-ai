"""Managing the sender's own keys from the conversation: list, add, replace, remove.

Rule one is the kind of conversation. A key is one person's credential, so it is only ever
handled where the conversation is one-to-one: a Telegram private chat, or SMS. In a group the
intent is refused before anything is parsed, stored, logged, or extracted — a key pasted into
a group is already visible to everyone in the room, and echoing any part of it back, or
storing it because a room said so, makes that worse rather than better.

Rule two is that the secret stays on this path. `parse_intent` runs before any model call, so
a message carrying something token-shaped is handled deterministically and the model never
sees it; `scrub` masks anything token-shaped in every body that goes on to the plan, the
memory extractor, telemetry, or a reply. Replies never show more than the last four
characters, which is enough for a person to tell two of their own keys apart.

Rule three is that nothing here names an integration. Every branch reads
`ambient_ai.tools.registry`, so connecting a second service is an entry in the registry and
no edit to this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import httpx

from ambient_ai.identity import clear_credential, lookup_sender, set_credential, upsert_sender
from ambient_ai.settings import MENTION
from ambient_ai.tools import registry
from ambient_ai.tools.github import GITHUB

Action = Literal["list", "add", "remove", "none"]

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
    always an add — somebody pasting a key means to connect it, and asking a model first
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


GROUP_REFUSAL = "Manage your tools in a private chat with me."
NO_TOOLS = "No tools connected."
OPTIONAL_NOTE = "All of them are optional — paste a key here to connect one."
DELETE_FAILED_NOTE = (
    " I couldn't delete your message — delete it yourself so the token isn't left in the chat."
)


def unknown_tool_text(tool: str) -> str:
    return f"I don't have an integration called {tool}.\n{status_text(None)}"


def status_line(integration: registry.Integration, profile) -> str:
    """One line per integration: what it is when absent, who it is connected as when present."""
    credential = profile.credential(integration.key) if profile else None
    if credential is None:
        return f"{integration.name}: not connected — {integration.description}"
    when = credential.added_at.date().isoformat() if credential.added_at else "earlier"
    who = f" as {credential.login}" if credential.login else ""
    return f"{integration.name}: connected{who} ({mask(credential.token)}), added {when}."


def status_text(profile) -> str:
    lines = [status_line(integration, profile) for integration in registry.integrations()]
    connected = bool(profile.credentials) if profile else False
    header = "Your integrations:" if connected else NO_TOOLS
    return "\n".join([header, *lines, OPTIONAL_NOTE])


async def handle(
    phone: str, intent: CredentialIntent, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, str]:
    """Apply one credential intent for one sender. Returns (reply text, telemetry outcome).

    The caller has already established that the conversation is private; this function never
    checks it again and never puts more than four characters of a key in what it returns.
    """
    profile = lookup_sender(phone)

    if intent.action in ("none", "list") and intent.tool not in registry.keys():
        return status_text(profile), "listed" if intent.action == "list" else "none"
    integration = registry.find(intent.tool)
    if integration is None:
        return unknown_tool_text(intent.tool), "unknown tool"

    if intent.action == "list":
        return status_text(profile), "listed"

    if intent.action == "add":
        if not intent.token:
            label = integration.field_label.lower()
            return f"Paste your {integration.name} {label} here and I'll connect it.", "no token"
        login = await integration.validate(intent.token, transport)
        if login is None:
            return (
                f"{integration.name} rejected that key, so I stored nothing. "
                "Paste a current one and I'll retry."
            ), "rejected"
        upsert_sender(phone)
        set_credential(phone, integration.key, intent.token, login=login)
        return f"{integration.name} connected as {login} ({mask(intent.token)}).", "stored"

    if intent.action == "remove":
        if not clear_credential(phone, integration.key):
            return (
                f"{integration.name} wasn't connected, so there was nothing to remove."
            ), "none"
        return f"{integration.name} disconnected.", "cleared"

    return status_text(profile), "none"
