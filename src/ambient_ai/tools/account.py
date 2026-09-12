"""Logging out: the sender asks to be forgotten, and everything this product holds goes.

Same two rules as `credentials`. The conversation must be one-to-one — a Telegram private
chat or SMS — because a group is somebody else's room and a member of it must not be able to
delete another person's memory, tools, and identity by typing into it. And the decision is
taken deterministically, before any model call: forgetting is destructive and irreversible
from the sender's side, so it cannot depend on a plan a model wrote.

Between the request and the deletion there is one confirmation, because `/forget` is one
keystroke away from `/tools` and a mis-sent word should not cost a person their memory. The
pending mark is a timestamp on the sender row; it expires after five minutes so a forgotten
request cannot be completed by a stray "confirm" days later.

The deletion itself runs widest-first and each step tolerates the state the one before it
left: the Cortex memory page, then every credentials row, then the sender row itself with
its Telegram mapping. A sender who asked to be forgotten is forgotten locally even when
Cortex is unreachable; the alternative is keeping their number because a third party was
down.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from ambient_ai.identity import (
    clear_forget_request,
    delete_sender,
    lookup_sender,
    mark_forget_requested,
    memory_keys,
)
from ambient_ai.memory.writer import forget_memory_pages
from ambient_ai.orchestration.context_agent import ToolsetFactory
from ambient_ai.settings import MENTION
from ambient_ai.telemetry import log, redact

Action = Literal["request", "confirm", "none"]

COMMAND = "/forget"
CONFIRM_WORDS = frozenset({"confirm", "yes", "confirmed", "do"})
CONFIRM_WINDOW = timedelta(minutes=5)

GROUP_REFUSAL = "Ask me privately to log out or forget you."
CONFIRM_PROMPT = (
    "This deletes your memory, your connected tools, and your number here. "
    "Reply `/forget confirm` within 5 minutes to go ahead."
)
DONE_TEXT = "Done. I've forgotten you. If you message me again I'll treat you as new."
EXPIRED_TEXT = (
    "That request expired or was never made. Send `/forget` again if you want me to "
    "forget you."
)
NOTHING_TEXT = "I have nothing stored about you."


@dataclass(frozen=True)
class ForgetIntent:
    action: Action = "none"


def parse_intent(body: str) -> ForgetIntent:
    """Read a forget intent off the raw message, before any model sees it."""
    words = body.replace(MENTION, " ").strip().split()
    if not words or words[0].lower() != COMMAND:
        return ForgetIntent()
    rest = [word.lower() for word in words[1:]]
    if rest and rest[0] in CONFIRM_WORDS:
        return ForgetIntent(action="confirm")
    return ForgetIntent(action="request")


async def handle(
    sender: str,
    intent: ForgetIntent,
    *,
    now: datetime | None = None,
    toolset_factory: ToolsetFactory | None = None,
) -> tuple[str, str]:
    """Apply one forget intent for one sender. Returns (reply text, telemetry outcome).

    The caller has already established that the conversation is private; this function never
    checks it again. `now` and `toolset_factory` are test seams.
    """
    moment = now or datetime.now(UTC)
    profile = lookup_sender(sender)
    if profile is None:
        return NOTHING_TEXT, "unknown"

    if intent.action == "request":
        mark_forget_requested(sender, moment)
        return CONFIRM_PROMPT, "requested"

    requested = profile.forget_requested_at
    if requested is None or moment - requested > CONFIRM_WINDOW:
        clear_forget_request(sender)
        return EXPIRED_TEXT, "expired"

    who = redact(sender)
    try:
        await forget_memory_pages(memory_keys(profile), toolset_factory=toolset_factory)
    except Exception as exc:  # noqa: BLE001 - a Cortex outage must not keep their number here
        log.emit("account.memory_failed", sender=who, error=type(exc).__name__)
    delete_sender(sender)
    return DONE_TEXT, "forgotten"
